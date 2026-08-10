"""`ToolRegistry` (Task 2) — the DEC-10 source of native-tool budgets and the
one place that builds them for a tag, adapted to Strands tool objects."""

from __future__ import annotations

from nora.agent import factory
from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.mcp import McpToolGroup
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
        message_cap=NATIVE_TOOL_BUDGETS["send_reply"], audience="tag", metrics=Metrics(),
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
        "now", "load_transcript", "send_reply",
        "group_members", "lookup_person", "about", "ask_memory", "search_memory",
        "calculate", "transit", "route",
        "place_info", "saved_places",
        "where_is",
        "media_fetch",
        "web_search",
        "weather",
        "remind", "list_reminders", "cancel_reminder",
        # DEC-79: what she is running, and what was in an earlier version.
        "version",
    }


def test_budgets_match_todays_table():
    assert dict(ToolRegistry().budgets()) == {
        "now": 4, "load_transcript": 4,
        # 3 — multi-message active (DEC-42): the cap on how many messages one
        # tag may send, raised 1→3 as the whole activation (the delivery path
        # already read this one number as the cap).
        "send_reply": 3,
        # Sized against model round trips, not against work — see
        # `NATIVE_TOOL_BUDGETS` for the per-tool reasoning.
        "group_members": 1, "lookup_person": 4, "about": 4,
        # 1 — the dialectic reasoning verb is slow (~14-19s), so one call per
        # turn is the ceiling; it is the escalation from `about` (design/24 §4).
        "ask_memory": 1, "search_memory": 4,
        # The one tool whose budget is sized by the QUESTION rather than by
        # cost: it is a pure function with no network and no clock, so four
        # calls are four model round trips and nothing else.
        "calculate": 8,
        # One message names two routes often enough (「68X 定 K73 快啲」) and
        # three almost never.
        "transit": 4,
        # Sized like transit: two place lookups plus a route, paid and external,
        # so a turn may reach for it twice but not more (DEC-46).
        "route": 4,
        # Sized like route: one Places lookup per call, a turn may ask after two
        # spots.
        "place_info": 4,
        # 1 like group_members: a fixed per-room list, same answer if called twice.
        "saved_places": 1,
        # One message can ask after two people; each call is one local table
        # read, so like `calculate` this is sized by the question (DEC-59).
        "where_is": 2,
        # The plugin-provided activation tool (DEC-50, activating DEC-48). It is
        # in `budgets()` ONLY so R-7 admits the `AgentSkills` activation call;
        # the registry does not BUILD it (see the tests below). Sized 1 like
        # `group_members`: re-activating a skill re-injects a body already in
        # context and cannot improve the answer.
        "skills": 1,
        # One message can name two images (a photo AND a screenshot); the
        # per-call vision timeout, not this number, is what keeps two describe
        # calls inside the loop budget (DEC-56).
        "media_fetch": 4,
        # One web verb (DEC-73, Tavily): sized by model round trips — search,
        # then refine once or twice; the doctrine says a couple is plenty.
        "web_search": 4,
        # One weather verb (DEC-72): current + warnings + outlook always, the
        # 9-day forecast on a flag. 2 — one HKO round usually answers, a second
        # covers a follow-up.
        "weather": 2,
        # The three reminder verbs (DEC-70), all 2 and sized by the
        # CONVERSATION rather than by cost: none leaves the host or the
        # database, so a call is one model round trip, and one message can
        # genuinely set two reminders or set one after cancelling another.
        # Raising them would buy nothing — the real bound on how many
        # reminders exist is `max_pending_per_requester`, checked inside
        # `remind` (§9), which no budget can substitute for.
        "remind": 2, "list_reminders": 2, "cancel_reminder": 2,
        # 2 — one call answers "what version are you", a second covers the
        # follow-up about an earlier one (DEC-79).
        "version": 2,
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
    from nora.agent.tools import NATIVE_TOOLS

    registry = ToolRegistry()
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeHoncho(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(),
    )
    # Every BUILT spec's budget matches `budgets()`. `budgets()` is a superset:
    # it also carries the plugin-provided `skills` budget (DEC-50), which R-7
    # admits but `build_tool_specs` does not build — so compare against the
    # non-plugin budgets, which are exactly the specs.
    non_plugin_budgets = {
        name: spec.budget for name, spec in NATIVE_TOOLS.items() if not spec.plugin
    }
    assert {s.name: s.budget for s in specs} == non_plugin_budgets


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
    # The registry BUILDS exactly its native tools — `tool_names()` excludes the
    # plugin-provided `skills` (DEC-50), which this agent (no `skills_dir`) does
    # not have. `NATIVE_TOOL_BUDGETS` is a SUPERSET now: it also carries the
    # `skills` budget so R-7 can admit the plugin's activation tool where it is
    # wired — but that budget is not a built tool here.
    assert set(agent.tool_names) == set(ToolRegistry().tool_names())
    assert set(agent.tool_names) == set(NATIVE_TOOL_BUDGETS) - {"skills"}
    assert "skills" in NATIVE_TOOL_BUDGETS


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
        name: spec.time_shiftable
        for name, spec in NATIVE_TOOLS.items()
        if not spec.plugin  # `skills` is not built as a spec (DEC-50)
    }
    assert {s.time_shiftable for s in specs} <= {None, False, True}


def test_the_live_only_tools_are_the_ones_whose_answer_decays():
    """`time_shiftable is False`, never `not time_shiftable` — the second
    sweeps in `calculate`, whose `None` means the question has no time in it at
    all, and would have the model told that arithmetic goes stale."""
    # `whereis` joins (DEC-59): its answer is the LATEST fix and decays — the
    # age rides in the result, but asked about LATER it has nothing.
    assert ToolRegistry().live_only_tools() == ("now", "transit", "where_is")


def test_the_capability_block_tells_her_which_tools_only_describe_now():
    """A declared field nothing renders is decoration. §5.1's whole point is
    that she must not quote a 112-second bus wait for a 19:30 departure, and
    the only place that can reach her is the prompt."""
    block = ToolRegistry().capability()
    assert "now and transit and where_is describe this moment only" in block
    assert "never quote one to answer about later" in block


def test_transit_is_declared_live_only_and_not_terminal():
    from nora.agent.tools import NATIVE_TOOLS
    assert NATIVE_TOOLS["transit"].time_shiftable is False
    assert NATIVE_TOOLS["transit"].terminal is False
    assert "transit" not in ToolRegistry().terminal_tools()


def test_web_search_is_the_only_web_tier_tool():
    """E1/DEC-77: the web tier is the last-resort rank the sourcing nudge names.
    `web_tier_tools` is derived from the table, so a second web verb would join it
    (and the rendered sentence) automatically."""
    assert ToolRegistry().web_tier_tools() == ("web_search",)


def test_the_capability_block_states_the_sourcing_priority():
    """A declared `source_tier` nothing renders is decoration (same argument as
    the clock line). The gate found the model web-searching a fact with a closer
    source; the only place a priority can reach her is the prompt."""
    block = ToolRegistry().capability()
    assert "prefer your own" in block
    assert "web_search, which reaches the open web and is the last resort" in block


def test_the_confusable_retrieval_tools_all_declare_a_source_tier():
    """The taxonomy must stay COMPLETE over the tools routing actually confuses —
    a new local tool added untagged would silently sit outside the priority. Pins
    the tier of each tool in the seed confusability clusters (D2)."""
    from nora.agent.tools import NATIVE_TOOLS
    expected = {
        "weather": "confirmed", "transit": "confirmed", "route": "confirmed",
        "place_info": "confirmed", "where_is": "confirmed",
        "saved_places": "remembered", "web_search": "web",
    }
    for name, tier in expected.items():
        assert NATIVE_TOOLS[name].source_tier == tier, name


# ---- the plugin-provided activation tool (DEC-50, activating DEC-48) ----------

def test_skills_is_budgeted_so_r7_admits_it_but_the_registry_does_not_build_it():
    """The `skills` activation tool is registered by `AgentSkills`
    (`nora.agent.factory`), not by `build_tool_specs`. It needs exactly ONE
    thing from this table — a budget, so R-7's default-deny lets `ReplyGuard`
    admit the activation call — and must appear in NOTHING the registry BUILDS
    or RENDERS, or the offline/eval compositions (which build the capability
    block but never wire the plugin) would promise a tool they do not have."""
    registry = ToolRegistry()
    # Admitted by R-7.
    assert "skills" in registry.budgets()
    assert registry.budgets()["skills"] == 1
    # Not built, not advertised, not in the catalogue gate.
    built = {t.tool_name for t in _built_tools(registry)}
    assert "skills" not in built
    assert "skills" not in registry.tool_names()
    assert "skills" not in registry.capability()
    # Neither terminal nor live-only — the honest fields.
    assert "skills" not in registry.terminal_tools()
    assert "skills" not in registry.live_only_tools()


def test_skills_declares_terminal_false_and_no_time_dimension():
    from nora.agent.tools import NATIVE_TOOLS
    spec = NATIVE_TOOLS["skills"]
    assert spec.plugin is True
    assert spec.terminal is False
    assert spec.time_shiftable is None


# ---- discovered MCP tools (spec §3, Task 4) -----------------------------------

class _FakeMcpTool:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name


def _mcp_groups() -> dict[str, McpToolGroup]:
    return {
        "pulse": McpToolGroup(
            budget=5,
            tools=(_FakeMcpTool("pulse_server_status"), _FakeMcpTool("pulse_weekly")),
        ),
    }


def test_budgets_excludes_mcp_tools_not_in_allowed_mcp():
    """R-7's default-deny must actually cover an unauthorized server's tool
    names (Finding 1, final-review): `budgets()` called with no `allowed_mcp`
    (the default, matching an unauthorized conversation) must not admit
    `pulse_server_status`/`pulse_weekly` at all — not merely leave them
    un-callable elsewhere."""
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets()
    assert "pulse_server_status" not in budgets
    assert "pulse_weekly" not in budgets
    # native budgets are untouched
    assert budgets["now"] == 4


def test_budgets_includes_mcp_tools_only_for_allowed_servers():
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets(allowed_mcp=frozenset({"pulse"}))
    assert budgets["pulse_server_status"] == 5
    assert budgets["pulse_weekly"] == 5
    # native budgets are untouched
    assert budgets["now"] == 4


def test_unauthorized_conversations_veto_message_never_names_mcp_tools():
    """The R-7 unknown-tool veto message is built from `ReplyGuard`'s
    `budgets` dict (`guards.py::_list_tool_names`). A conversation not
    authorized for `pulse` must never have that server's tool names appear in
    the message the MODEL sees when it hallucinates a wrong tool name —
    the spec's non-disclosure claim, concretely (Finding 1)."""
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    budgets = registry.budgets()  # allowed_mcp defaults empty: unauthorized
    guard = ReplyGuard(
        conversation_id="g1", budgets=budgets, terminal=registry.terminal_tools(),
        metrics=Metrics(),
    )
    message = guard.evaluate("totally_made_up_tool", {})
    assert message is not None
    assert "pulse_server_status" not in message
    assert "pulse_weekly" not in message


def test_capability_and_friends_are_unaffected_by_mcp_tools():
    plain = ToolRegistry()
    with_mcp = ToolRegistry(mcp_tools=_mcp_groups())
    assert with_mcp.capability() == plain.capability()
    assert with_mcp.tool_names() == plain.tool_names()
    assert with_mcp.terminal_tools() == plain.terminal_tools()
    assert with_mcp.live_only_tools() == plain.live_only_tools()


def test_build_omits_mcp_tools_when_allowed_mcp_is_empty():
    tools = _built_tools(ToolRegistry(mcp_tools=_mcp_groups()))
    names = {t.tool_name for t in tools}
    assert "pulse_server_status" not in names
    assert "pulse_weekly" not in names


def test_build_includes_only_the_allowed_mcp_servers_tools():
    registry = ToolRegistry(mcp_tools=_mcp_groups())
    tools = registry.build(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeHoncho(), deliverer=_deliverer(),
        metrics=Metrics(), room=_room(), allowed_mcp=frozenset({"pulse"}),
    )
    names = {t.tool_name for t in tools}
    assert {"pulse_server_status", "pulse_weekly"} <= names
