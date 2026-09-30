"""`nora.app.cognition_wiring` and the session runner's wiring seams (migration
step 3a-iii, DEC-244): which read tools a session holds, its budget, what it
is told, how it is stopped and how it is traced.

No model and no Strands: the agent runner is a recording double and the
tool source is a fake that answers like the registry's (a list of tool
objects for the names it was asked for).
"""

from __future__ import annotations

import dataclasses
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
    read_tool_budgets,
    read_tools_for,
    require_session_hook,
    units_with_home_rooms,
)
from nora.config import DreamBudgetConfig
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.obs.metrics import Metrics
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_790_000_000_000
HOME = Room("signal", "home")
OTHER = Room("discord", "other")
READ_ONLY = frozenset(n for n, t in NATIVE_TOOLS.items() if t.read_only)


# ---- the read catalogue ------------------------------------------------------

def test_the_catalogue_is_the_designs_and_names_no_live_social_read():
    """30 §2 / 15 §4: the warehouse queries and the tiers' reads. The live
    `instagram_*`, `x_*` and `linkedin_*` reads stay on the tag path (20 §2)."""
    assert {"record_query", "note_read", "about", "event_search"} <= COGNITION_READ_CATALOGUE
    live_social = {n for n in NATIVE_TOOLS if n.startswith(("instagram_", "x_", "linkedin_"))}
    assert live_social, "positive control: the tool table has live social reads"
    assert not (COGNITION_READ_CATALOGUE & live_social)


def test_every_tool_the_session_would_wire_exists_and_is_read_only():
    surface = read_surface(read_only=READ_ONLY, enabled_flags={"record_query.enabled"})
    assert surface.wired, "positive control"
    assert surface.wired <= READ_ONLY
    assert all(NATIVE_TOOLS[n].read_only for n in surface.wired)


def test_the_designs_tools_the_tree_does_not_have_are_named_missing_not_dropped():
    surface = read_surface(read_only=READ_ONLY, enabled_flags={"record_query.enabled"})
    assert surface.missing == {
        "rollup_read", "memo_read", "character_read", "standing_read", "social_items",
    }
    assert surface.wired | surface.missing == COGNITION_READ_CATALOGUE


def test_record_query_is_withheld_until_its_flag_is_on():
    off = read_surface(read_only=READ_ONLY, enabled_flags=())
    on = read_surface(read_only=READ_ONLY, enabled_flags={"record_query.enabled"})
    assert "record_query" in off.withheld and "record_query" not in off.wired
    assert "record_query" in on.wired and not on.withheld


def test_an_act_verb_is_never_wired_even_when_the_catalogue_names_it():
    """The read-only set decides, not the catalogue: a name that is not in it
    is `missing`, never wired."""
    surface = read_surface(read_only=frozenset({"note_read"}), enabled_flags=())
    assert surface.wired == {"note_read"}
    assert "about" in surface.missing


# ---- budgets ------------------------------------------------------------------

def test_read_tool_budgets_apply_the_table_then_the_deployment_then_the_adaptor():
    table = {"note_read": 3, "about": 4, "where_been": 2}
    got = read_tool_budgets(
        {"note_read", "about", "where_been", "no_row"}, table=table,
        overrides={"about": 9, "note_read": 9}, adaptor_overrides={"note_read": 16},
    )
    assert got == {"note_read": 16, "about": 9, "where_been": 2}   # no row: absent = denied


def test_a_per_tool_ceiling_for_a_tool_the_session_does_not_hold_refuses_to_start():
    from nora.app.cognition_wiring import check_tool_call_overrides

    check_tool_call_overrides({"note_read": 4}, held={"note_read", "close"})
    with pytest.raises(ValueError, match="lookup_person"):
        check_tool_call_overrides({"lookup_person": 4}, held={"note_read"})


def test_the_session_budget_carries_every_field_the_dreamer_budget_gives_it():
    """A field added to one side and not the other would be dropped in silence
    (`_adaptor_budget`'s own reason). The Dreamer's `tool_calls` map is the one
    field the session takes from the per-tool path instead."""
    configured = DreamBudgetConfig(
        turns=7, total_tokens=11, output_tokens=13, timeout_seconds=17.0,
        max_memory_calls=3, max_tool_calls=19,
    )
    got = cognition_budget(configured)
    assert isinstance(got, AdaptorBudget)
    for f in dataclasses.fields(AdaptorBudget):
        assert getattr(got, f.name) == getattr(configured, f.name), f.name


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
    tools = build("friends", Room("signal", "g1"))
    assert sorted(t.__name__ for t in tools) == ["about", "note_read"]
    (call,) = source.calls
    assert call["key"] == "g1" and call["unit"] is None       # scope-resolved: the room's own
    assert call["adaptor"] == "cognition"
    assert call["grant"] == cognition_grant() == AdaptorGrant(home_room=True)
    assert call["grant"].acts == frozenset()                   # no act verb, ever


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
    metrics: Any = None, **kw: Any,
) -> tuple[CognitionSessionRunner, InMemoryStimulusStore, InMemoryCognitionDryRunStore]:
    ledger = InMemoryCognitionDryRunStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    for wid in wake_ids:
        stimuli.seed_wake("u", wid)

    async def const(*a: Any, **k: Any) -> Any:
        return kw.pop("_value", ())

    runner = CognitionSessionRunner(
        dry_run_store=ledger, stimulus_store=stimuli, agent_runner=agent,
        home_rooms={"u": HOME}, read_tools_for=read_tools_for or (lambda unit, room: []),
        read_only_tools=frozenset({"note_read", "about"}),
        skill_text_for=kw.pop("skill_text_for", lambda kinds: ""),
        doctrine_text="FIXED", doctrine_sha256="fixed-sha",
        open_note_titles=const, open_proposed_events=const, prior_standing=const,
        pending_self_wakes=const, pending_mirrored_reminds=const,
        session_mode="shadow",
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
    asked: list[Room] = []

    def reads(unit: str, room: Room) -> list[Any]:
        asked.append(room)
        return [_tool("note_read")]

    agent = _Agent()
    runner, _s, _l = _runner(agent=agent, read_tools_for=reads)
    await _wake(runner, (_stim(1, OTHER),))
    assert asked == [OTHER]
    assert "Your read tools look through room discord:other." in agent.prompts[0]


async def test_an_agenda_spanning_rooms_binds_the_reads_once_to_the_home_room():
    """A tool name is one tool: two rooms' `note_read` cannot both be
    `note_read` (Strands keeps the last silently). One binding, the unit's
    home room, and the prompt says which."""
    asked: list[Room] = []

    def reads(unit: str, room: Room) -> list[Any]:
        asked.append(room)
        return [_tool("note_read"), _tool("about")]

    agent = _Agent()
    runner, _s, _l = _runner(agent=agent, read_tools_for=reads)
    await _wake(runner, (_stim(1, HOME), _stim(2, OTHER)))
    assert asked == [HOME]                       # once, never per room
    assert agent.tool_names[0].count("note_read") == 1
    assert "Your read tools look through room signal:home." in agent.prompts[0]


async def test_a_read_tool_handed_twice_is_refused_not_kept_last():
    runner, _s, _l = _runner(
        agent=_Agent(), read_tools_for=lambda u, r: [_tool("note_read"), _tool("note_read")],
    )
    with pytest.raises(NotReadOnlyToolError, match="handed twice"):
        await _wake(runner, (_stim(1, HOME),))


async def test_the_prompt_says_what_time_it_is_and_how_old_each_item_is():
    """A judgement that turns on WHEN needs the time in its input: an agenda
    with none reads every item as «just now»."""
    agent = _Agent()
    runner, _s, _l = _runner(agent=agent)
    await _wake(runner, (_stim(1, HOME, at=NOW - 90 * 60_000),))
    first, _reads, item = agent.prompts[0].splitlines()[:3]
    assert first.startswith("It is ") and "(UTC)" in first
    assert "This is your first look at this group." in first
    assert "1 h 30 min ago" in item, item


async def test_probe_runs_one_hand_written_item_and_writes_no_outcome():
    """The seam `tool_selection_cognition` drives: same tools, same doctrine,
    same agent runner; no wake, so no outcome and no carry."""
    agent = _Agent()
    runner, stimuli, ledger = _runner(agent=agent, read_tools_for=lambda u, r: [_tool("note_read")])
    result = await runner.probe(unit="u", summary="room A has 2 new messages", now_ms=NOW)
    assert result.stop_reason == "end_turn"
    assert "room A has 2 new messages" in agent.prompts[0]
    assert {"close", "note_create", "remind", "wake_at", "note_read"} <= set(agent.tool_names[0])
    assert len(ledger.sessions) == 1 and stimuli.outcomes == {}


async def test_probe_refuses_when_sessions_are_off():
    runner, _s, _l = _runner(agent=_Agent())
    runner._session_mode = "off"
    with pytest.raises(RuntimeError, match="session_mode"):
        await runner.probe(unit="u", summary="x", now_ms=NOW)


async def test_a_closed_session_is_counted_by_outcome():
    metrics = Metrics()
    runner, _s, _l = _runner(agent=_Agent(), metrics=metrics)
    await _wake(runner, (_stim(1, HOME),))
    assert metrics.get("nora_cognition_session_ended_total", outcome="closed") == 1
    assert metrics.get("nora_cognition_session_total", mode="shadow") == 1


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


async def test_the_guard_holds_every_ceiling_a_dreamer_sample_holds(monkeypatch):
    """The per-pass memory and tool ceilings, the result caps and the accepted
    argument names (R-99), and the per-tool budgets: the registry's number for a
    read tool, the session default for its own verbs."""
    async def invoke(*a: Any, **k: Any) -> Any:
        return SimpleNamespace(stop_reason="end_turn")

    _patch_agent(monkeypatch, invoke)
    await _go(_live(
        read_tool_budgets={"note_read": 16}, param_names={"note_read": frozenset({"x"})},
        result_caps={"about": 4000}, memory_tools=frozenset({"about"}), tool_budget=20,
    ))
    (guard,) = _Guard.seen
    assert guard["adaptor"] == "cognition"
    assert guard["budgets"] == {"note_read": 16, "close": 20}
    assert (guard["memory_budget"], guard["pass_budget"]) == (6, 60)
    assert guard["param_names"] == {"note_read": frozenset({"x"})}
    assert guard["result_caps"] == {"about": 4000}
    assert guard["memory_tools"] == frozenset({"about"})


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
        async def cancel_in_flight(self) -> None:
            order.append("evaluator")

    class Runner:
        async def shutdown(self) -> None:
            order.append("runner")

    stop = CognitionShutdown(evaluator=Evaluator(), runner=Runner())
    await stop()
    await stop()                                   # a second shutdown is a no-op
    assert order == ["evaluator", "runner"] and stop.done


def test_units_without_a_home_room_are_named_not_woken():
    homes = {"friends": SimpleNamespace(platform="signal", primary_id="g1")}
    assert units_with_home_rooms(["friends", "wedding"], homes) == (["friends"], ["wedding"])


def test_the_boot_check_refuses_a_session_mode_that_built_no_session():
    hooked = SimpleNamespace(has_wake_hook=True)
    bare = SimpleNamespace(has_wake_hook=False)
    require_session_hook(session_mode="off", runner=None, evaluator=None)     # off asks for none
    require_session_hook(session_mode="shadow", runner=object(), evaluator=hooked)
    with pytest.raises(RuntimeError, match="no session runner"):
        require_session_hook(session_mode="shadow", runner=None, evaluator=hooked)
    with pytest.raises(RuntimeError, match="no session hook"):
        require_session_hook(session_mode="shadow", runner=object(), evaluator=bare)
    with pytest.raises(RuntimeError, match="no session hook"):
        require_session_hook(session_mode="shadow", runner=object(), evaluator=None)


async def test_the_app_stops_the_cognition_sessions_before_it_closes_the_client_and_the_audit():
    """`App.shutdown` (the `finally` of `main`) is where the design's open
    choice (a) lands: the sessions are cancelled BEFORE the model client and
    the database they use are closed, and stopping twice is harmless."""
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
        cognition_shutdown=_Stop(), client=_Closable("client"), audit=_Closable("audit"),
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
    assert outcome.source == "fail_closed" and "session_mode is off" in (outcome.error or "")


async def test_a_probe_that_raised_is_an_error_never_a_pass():
    from evals.rebuilt.entry import _cognition_probe

    class _Runner:
        async def probe(self, **kw: Any) -> None:
            raise RuntimeError("the provider fell over")

    subject = SimpleNamespace(deps=SimpleNamespace(cognition_runner=_Runner()), unit="friends")
    outcome = await _cognition_probe(subject, _case(), now_ms=NOW)
    assert outcome.source == "fail_closed" and "fell over" in (outcome.error or "")
