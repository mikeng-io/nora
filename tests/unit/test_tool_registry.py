"""`ToolRegistry` (Task 2) — the DEC-10 source of native-tool budgets and the
one place that builds them for a tag, adapted to Strands tool objects."""

from __future__ import annotations

from nora.agent import factory
from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOL_BUDGETS
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget
from nora.obs.metrics import Metrics
from tests.fakes import FakeHoncho, SinkSender

TARGET = ConversationTarget("signal", "g1", "group")


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>", doctrine_text="be helpful",
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def resolve(self, platform: str, sender_id: str):
        return None


def _deliverer() -> Deliverer:
    return Deliverer(
        store=_FakeStore(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="t1", nora_sender_id="nora",
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], metrics=Metrics(),
    )


def _room() -> Room:
    return Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_FakePersons(),
    )


def _built_tools(registry: ToolRegistry) -> list:
    return registry.build(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeHoncho(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(),
    )


def test_the_registry_exposes_every_declared_tool():
    tools = _built_tools(ToolRegistry())
    assert {t.tool_name for t in tools} == {
        "now", "read_buffer", "send_reply",
        "group_members", "lookup_person", "about", "calculate", "transit",
    }


def test_budgets_match_todays_table():
    assert dict(ToolRegistry().budgets()) == {
        "now": 2, "read_buffer": 2, "send_reply": 1,
        # Sized against model round trips, not against work — see
        # `NATIVE_TOOL_BUDGETS` for the per-tool reasoning.
        "group_members": 1, "lookup_person": 2, "about": 2,
        # The one tool whose budget is sized by the QUESTION rather than by
        # cost: it is a pure function with no network and no clock, so four
        # calls are four model round trips and nothing else.
        "calculate": 4,
        # One message names two routes often enough (「68X 定 K73 快啲」) and
        # three almost never.
        "transit": 2,
    }


def test_calculate_is_not_terminal_and_ends_no_turn():
    """`terminal` truncates a turn when it is wrong, and arithmetic is the
    middle of an answer rather than the end of one — she works a total out and
    then still has to say it. `time_shiftable` has no meaning here either way:
    unlike `now`, the answer to `1485/7` does not depend on when it is asked."""
    assert ToolRegistry().terminal_tools() == frozenset({"send_reply"})


def test_budgets_do_not_require_runtime_dependencies():
    """`budgets()` is what `ReplyGuard` is constructed with, at a point in the
    pipeline before any tag-specific collaborator (store, deliverer, ...)
    exists — it must not need them."""
    assert ToolRegistry().budgets()


def test_specs_carry_the_same_budgets_as_budgets():
    registry = ToolRegistry()
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeHoncho(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(),
    )
    assert {s.name: s.budget for s in specs} == dict(registry.budgets())


def test_adapting_produces_objects_the_agent_factory_accepts():
    client = factory.create_client(base_url="http://localhost:20128/v1", api_key="test-key")
    model = factory.create_model_provider(
        model="deepseek-v4-flash", max_tokens=400, temperature=0.7, client=client,
    )
    tools = _built_tools(ToolRegistry())
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=tools, pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=ToolRegistry().budgets(),
                        terminal=ToolRegistry().terminal_tools(), metrics=Metrics()),
        trace_attributes={},
        retry=factory.build_retry_policy(timeout_seconds=30.0),
    )
    assert set(agent.tool_names) == set(NATIVE_TOOL_BUDGETS)


# ---- time-shiftability (design/14 §5.1, DEC-40) ------------------------------

def test_every_tool_declares_whether_it_can_be_asked_about_another_moment():
    """§5.1's field, and it is three-valued: `None` means the tool has no time
    dimension, `False` means live-only, `True` means it takes a target time.
    `None` is a real answer rather than an omission, so what a test can hold is
    that the SPEC carries the table's value — `_spec()` silently dropping the
    field would give every tool the dataclass default, and a live-only tool
    quietly declaring it never goes stale is the failure worth catching."""
    from nora.agent.tools import NATIVE_TOOLS
    registry = ToolRegistry()
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeHoncho(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(),
    )
    assert {s.name: s.time_shiftable for s in specs} == {
        name: spec.time_shiftable for name, spec in NATIVE_TOOLS.items()
    }
    assert {s.time_shiftable for s in specs} <= {None, False, True}


def test_the_live_only_tools_are_the_ones_whose_answer_decays():
    """`time_shiftable is False`, never `not time_shiftable` — the second
    sweeps in `calculate`, whose `None` means the question has no time in it at
    all, and would have the model told that arithmetic goes stale."""
    assert ToolRegistry().live_only_tools() == ("now", "transit")


def test_the_capability_block_tells_her_which_tools_only_describe_now():
    """A declared field nothing renders is decoration. §5.1's whole point is
    that she must not quote a 112-second bus wait for a 19:30 departure, and
    the only place that can reach her is the prompt."""
    block = ToolRegistry().capability()
    assert "now and transit describe this moment only" in block
    assert "never quote one to answer about later" in block


def test_transit_is_declared_live_only_and_not_terminal():
    from nora.agent.tools import NATIVE_TOOLS
    assert NATIVE_TOOLS["transit"].time_shiftable is False
    assert NATIVE_TOOLS["transit"].terminal is False
    assert "transit" not in ToolRegistry().terminal_tools()
