"""`ToolRegistry.surface()` — the one builder every loop takes its tools from.

Phase B of `design/tool-catalogue/`. Three properties, each pinned here:

1. `defer=False` is byte-for-byte the old build: same tools, no loader. Every
   production caller ships in this mode until phase C flips the switch, so a
   difference here would be a behaviour change nobody decided.
2. The catalogue, the resident set and the shelf are derived from ONE admitted
   set. The catalogue cannot advertise a tool the turn cannot reach — the
   defect a declared-table catalogue has in a Signal room.
3. `load_tools` actually works inside a Strands loop: the named tool is absent
   from the first model call's `tools`, present in the next, and dispatches.
   That is gate 2b's replacement (`05-measurement.md`), run against the real
   SDK rather than asserted from reading it.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterable, Sequence
from typing import Any

import pytest
import tiktoken
from strands import Agent
from strands.models import Model

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.surface import (
    DISCORD_ONLY_TOOLS,
    DREAMER_ONLY_TOOLS,
    LOAD_TOOLS_MAX_NAMES,
    ToolScope,
    ToolShelf,
    load_onto,
)
from nora.agent.tools import HOT_TOOLS, LOADER_TOOL
from nora.obs.metrics import Metrics
from nora.testing.fakes import FakeMemoryStore
from tests.unit.test_tool_registry import _deliverer, _FakePersons, _FakeStore


def _surface(platform: str = "signal", **kw: Any):
    room = Room(platform=platform, conversation_id="g1", roster_source=None,
                persons=_FakePersons())
    return ToolRegistry().surface(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=room, **kw,
    )


def _names(tools: list[Any]) -> set[str]:
    return {t.tool_name for t in tools}


# ---- 1. defer=False is the old build ---------------------------------------


def test_without_deferral_every_admitted_tool_is_resident_and_there_is_no_loader():
    surface = _surface()
    assert _names(surface.tools) == surface.admitted
    assert surface.resident == surface.admitted
    assert not surface.shelf
    assert LOADER_TOOL not in _names(surface.tools)


def test_build_is_the_surface_without_deferral():
    """`build()` delegates — there is one path, not two that could drift."""
    room = Room(platform="signal", conversation_id="g1", roster_source=None,
                persons=_FakePersons())
    built = ToolRegistry().build(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(), honcho=FakeMemoryStore(), deliverer=_deliverer(),
        metrics=Metrics(), room=room,
    )
    assert _names(built) == _names(_surface().tools)


# ---- 2. one admitted set ---------------------------------------------------


def test_deferral_keeps_the_hot_set_resident_and_shelves_the_rest():
    surface = _surface(defer=True)
    assert surface.resident == surface.admitted & HOT_TOOLS
    assert surface.shelf.names() == surface.admitted - HOT_TOOLS
    assert _names(surface.tools) == surface.resident | {LOADER_TOOL}
    # `send_reply` is structural: a turn that had to fetch its terminal verb
    # before it could reply is a turn that can fail to end.
    assert "send_reply" in surface.resident


def test_a_signal_catalogue_names_no_tool_a_signal_turn_cannot_load():
    """The defect this module exists for. `tool_catalogue()` renders the
    DECLARED table; a Signal room rendered from it would advertise the six
    music verbs, and `load_tools` would then answer `unknown` for each."""
    surface = _surface(platform="signal", defer=True)
    named = {line.split(" — ")[0][2:] for line in surface.catalogue.splitlines()}
    assert named == surface.admitted
    assert not named & DISCORD_ONLY_TOOLS
    assert not named & DREAMER_ONLY_TOOLS
    assert named <= surface.resident | surface.shelf.names()


def test_the_catalogue_marks_exactly_the_resident_tools():
    surface = _surface(platform="discord", defer=True)
    marked = {
        line.split(" — ")[0][2:] for line in surface.catalogue.splitlines()
        if line.startswith("* ")
    }
    assert marked == surface.resident


def test_a_grant_is_a_ceiling_on_every_part_of_the_surface():
    surface = _surface(grant=frozenset({"now", "where_is", "play_music"}))
    # `play_music` is granted but not admitted on Signal: the grant is a
    # ceiling, never a way past the platform cut.
    assert surface.admitted == {"now", "where_is"}
    assert _names(surface.tools) == {"now", "where_is"}


def test_the_dreamer_only_verb_needs_the_dreamer():
    assert "search_memory_workspace" not in _surface().admitted
    assert "search_memory_workspace" in _surface(dream_adaptor="proactive").admitted


def test_the_declared_table_scope_admits_everything():
    scope = ToolScope(platform=None)
    assert all(scope.admits(n) for n in DISCORD_ONLY_TOOLS | DREAMER_ONLY_TOOLS)


# ---- 3. load_tools -----------------------------------------------------------


class _Registry:
    def __init__(self, resident: set[str]) -> None:
        self.registry = dict.fromkeys(resident)
        self.dynamic_tools: dict[str, Any] = {}

    def register_dynamic_tool(self, tool: Any) -> None:
        self.dynamic_tools[tool.tool_name] = tool


class _Tool:
    def __init__(self, name: str) -> None:
        self.tool_name = name


def _agent(resident: set[str]) -> Any:
    class _A:
        tool_registry = _Registry(resident)
    return _A()


def _shelf(*names: str) -> ToolShelf:
    shelf = ToolShelf()
    shelf.stock({n: _Tool(n) for n in names})
    return shelf


def test_a_partial_resolve_is_ok_and_says_which_half_failed():
    agent = _agent({"now"})
    out = load_onto(agent, _shelf("route"), ["route", "teleport", "now"])
    assert out["status"] == "ok"
    assert out["loaded"] == ["route"]
    assert out["unknown"] == ["teleport"]
    assert out["already_loaded"] == ["now"]
    assert "route" in agent.tool_registry.dynamic_tools


def test_no_resolvable_name_is_nothing_found():
    out = load_onto(_agent(set()), _shelf("route"), ["teleport"])
    assert out["status"] == "nothing_found"


def test_no_registry_is_unavailable_not_nothing_found():
    """conduct.md's own rule: a lookup that could not run is not a lookup that
    found nothing, and the loader must not be the first thing to blur them."""
    out = load_onto(object(), _shelf("route"), ["route"])
    assert out["status"] == "unavailable"


def test_the_per_call_cap_refuses_the_overflow_and_loads_the_rest():
    names = [f"t{i}" for i in range(LOAD_TOOLS_MAX_NAMES + 2)]
    agent = _agent(set())
    out = load_onto(agent, _shelf(*names), names)
    assert out["loaded"] == names[:LOAD_TOOLS_MAX_NAMES]
    assert out["refused"] == names[LOAD_TOOLS_MAX_NAMES:]
    assert set(agent.tool_registry.dynamic_tools) == set(names[:LOAD_TOOLS_MAX_NAMES])


def test_a_non_ok_load_carries_a_note():
    """ADR 0015 §3: the description only NAMES `nothing_found` and
    `unavailable`; how to report them is the result's `note`."""
    cases = {
        "nothing_found": load_onto(_agent(set()), _shelf("route"), ["teleport"]),
        "unavailable": load_onto(object(), _shelf("route"), ["route"]),
    }
    for status, out in cases.items():
        assert out["status"] == status
        assert isinstance(out.get("note"), str) and out["note"].strip(), status
        assert "\u2014" not in out["note"] and "\u2013" not in out["note"], status


def test_an_ok_load_notes_only_the_lists_that_are_not_empty():
    """`unknown` and `refused` each carry their rule only when that list has a
    name in it; a clean load is the bare result."""
    clean = load_onto(_agent(set()), _shelf("route"), ["route"])
    assert "note" not in clean

    partial = load_onto(_agent(set()), _shelf("route"), ["route", "teleport"])
    assert "unknown" in partial["note"] and "refused" not in partial["note"]

    names = [f"t{i}" for i in range(LOAD_TOOLS_MAX_NAMES + 1)]
    over = load_onto(_agent(set()), _shelf(*names), names)
    assert "refused" in over["note"] and "unknown" not in over["note"]


def test_loading_the_same_tool_twice_is_not_an_error():
    """`register_dynamic_tool` raises on a duplicate; asking twice is a
    wasted round trip, not a crash."""
    agent = _agent(set())
    shelf = _shelf("route")
    load_onto(agent, shelf, ["route"])
    out = load_onto(agent, shelf, ["route"])
    assert out["status"] == "ok"
    assert out["already_loaded"] == ["route"]


def test_the_loader_schema_does_not_ask_the_model_for_the_context():
    """`tool_context` is Strands' injection, not an argument. If `adapt()`
    stops passing `context=True`, the model is asked to supply it."""
    surface = _surface(defer=True)
    loader = next(t for t in surface.tools if t.tool_name == LOADER_TOOL)
    props = loader.tool_spec["inputSchema"]["json"]["properties"]
    assert set(props) == {"names"}


class _LoadThenCall(Model):
    """Call 1: `load_tools(["calculate"])`. Call 2: `calculate("6*7")`.
    Call 3: stop. Records the tool names each call was offered."""

    _SCRIPT: Sequence[tuple[str, dict] | None] = (
        (LOADER_TOOL, {"names": ["calculate"]}),
        ("calculate", {"expression": "6*7"}),
        None,
    )

    def __init__(self) -> None:
        self.offered: list[set[str]] = []
        self.results: list[dict] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None,
                                **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: Any, tool_specs: Any = None,
                     *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self.offered.append({spec["name"] for spec in tool_specs or []})
        for message in messages:
            for block in message.get("content", []):
                if "toolResult" in block:
                    text = block["toolResult"]["content"][0]["text"]
                    try:
                        self.results.append(json.loads(text))
                    except ValueError:
                        # Strands' own error results are plain text
                        # («Unknown tool: …»), not a tool's JSON.
                        self.results.append({"text": text})
        step = self._SCRIPT[len(self.offered) - 1]
        yield {"messageStart": {"role": "assistant"}}
        if step is None:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            name, args = step
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": name, "toolUseId": f"t{len(self.offered)}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {
                "input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }}


async def test_a_shelved_tool_is_absent_then_loaded_then_dispatched():
    """Gate 2b's replacement, end to end against strands 1.50.2: the loader
    registers onto the running agent, the NEXT call carries the tool, and the
    executor dispatches it. Revert `adapt()`'s `context=` and the loader gets
    no agent; drop the registration and call 2 is not offered `calculate`."""
    surface = _surface(defer=True)
    assert "calculate" in surface.shelf.names()
    model = _LoadThenCall()
    agent = Agent(model=model, tools=surface.tools, callback_handler=None,
                  load_tools_from_directory=False)

    await agent.invoke_async("what is six times seven")

    assert "calculate" not in model.offered[0]
    assert LOADER_TOOL in model.offered[0]
    assert "calculate" in model.offered[1]
    assert {"status": "ok", "loaded": ["calculate"], "already_loaded": [],
            "unknown": [], "refused": []} in model.results
    assert any(r.get("result") == "42" for r in model.results)


def test_a_loaded_schema_is_never_a_lookup_for_r97():
    """`load_tools` is read-only AND `three_state`, which is exactly what
    `lookup_tools()` derives a lookup from. R-97 asks whether a turn came back
    with something; a successful load answering «yes» would let it silence a
    reply after a lookup that found nothing."""
    lookups = ToolRegistry().lookup_tools()
    assert LOADER_TOOL not in lookups
    assert "web_search" in lookups  # positive control: the derivation still runs


class _CallDirectly(_LoadThenCall):
    """Calls a SHELVED tool straight from its catalogue line, never loading it
    — what arm B measured her doing on most deferred turns."""

    _SCRIPT: Sequence[tuple[str, dict] | None] = (
        ("calculate", {"expression": "6*7"}),
        None,
    )


async def test_a_shelved_tool_called_by_name_runs_on_the_first_call():
    """The measured failure (design/tool-catalogue arm B): she called
    `calculate` from its catalogue line without loading it, Strands answered
    «Unknown tool», and she paid a round trip to load and retry. `ShelfLoader`
    registers it at the call. Drop `surface.hooks` from the agent and this
    turn's first result is an error instead of 42."""
    surface = _surface(defer=True)
    model = _CallDirectly()
    agent = Agent(model=model, tools=surface.tools, hooks=list(surface.hooks),
                  callback_handler=None, load_tools_from_directory=False)

    await agent.invoke_async("what is six times seven")

    assert "calculate" not in model.offered[0]
    assert "calculate" in model.offered[1]   # registered for the rest of the turn
    assert model.results and model.results[0].get("result") == "42"


def test_nothing_shelved_means_no_hook():
    assert _surface().hooks == ()
    assert len(_surface(defer=True).hooks) == 1


async def test_a_name_off_the_shelf_is_left_unknown():
    """Only THIS turn's shelf: a tool the scope did not admit (music, on a
    Signal room) stays «Unknown tool» — the hook cannot widen the scope."""
    surface = _surface(platform="signal", defer=True)
    assert "play_music" not in surface.shelf.names()

    class _CallMusic(_LoadThenCall):
        _SCRIPT = (("play_music", {"query": "x"}), None)

    model = _CallMusic()
    agent = Agent(model=model, tools=surface.tools, hooks=list(surface.hooks),
                  callback_handler=None, load_tools_from_directory=False)
    await agent.invoke_async("play something")
    assert "play_music" not in model.offered[1]
    assert any("Unknown tool" in r.get("text", "") for r in model.results)


# ---- the two Dreamer-only reads of PR B (DEC-246) -----------------------------------------

_LOOP_ONLY = ("rollup_read", "standing_read", "character_read")


def test_the_dreamer_loop_reads_are_never_a_tag_turns_tools():
    """`standing_read` is the session's own position and `character_read` waits
    on a `tool_selection` run: a reply neither pays their prompt nor can route
    to them, in a Discord room or a Signal one."""
    from nora.agent.surface import DREAMER_LOOP_TOOLS, TAG_READABLE_LOOP_TOOLS

    assert set(_LOOP_ONLY) == DREAMER_LOOP_TOOLS
    loop_only = DREAMER_LOOP_TOOLS - TAG_READABLE_LOOP_TOOLS
    assert loop_only == {"standing_read", "character_read"}
    assert loop_only <= DREAMER_ONLY_TOOLS
    for platform in ("signal", "discord"):        # `None` is the DECLARED table: everything
        tag = ToolScope(platform=platform, dreamer=False)
        assert not any(tag.admits(n) for n in loop_only), platform
        assert all(ToolScope(platform=platform, dreamer=True).admits(n) for n in _LOOP_ONLY)


def test_rollup_read_is_a_tag_turns_tool_and_no_other_occasions():
    """«Where have we been together lately» reaches the outing rows from a tag
    turn (unified-memory step 0). A fire turn and the sub-agent worker are not
    a person asking, and stay without it."""
    from nora.agent.surface import TAG_READABLE_LOOP_TOOLS

    assert {"rollup_read"} == TAG_READABLE_LOOP_TOOLS
    assert "rollup_read" not in DREAMER_ONLY_TOOLS
    for platform in ("signal", "discord", "instagram"):
        assert ToolScope(platform=platform).admits("rollup_read"), platform
        assert ToolScope(platform=platform, dreamer=True).admits("rollup_read"), platform
        assert not ToolScope(platform=platform, tag_turn=False).admits("rollup_read"), platform
        assert not ToolScope(
            platform=platform, tag_turn=False, worker=True).admits("rollup_read"), platform


def test_the_relevance_gates_capability_digest_does_not_offer_what_a_reply_cannot_do():
    from nora.agent.registry import ToolRegistry

    wired = ToolRegistry(rollup=object(), standing_head=object(), culture=object())
    digest = wired.capability_digest()
    assert not any(n in digest for n in ("standing_read", "character_read"))
    assert "rollup_read" in digest, "a tag turn can call it, so the gate may count on it"
    assert "where_been" in digest, "positive control: the digest lists the tag path's reads"


def test_each_read_is_withheld_from_every_surface_until_its_read_is_wired():
    from nora.agent.registry import ToolRegistry

    assert set(_LOOP_ONLY) <= ToolRegistry()._withheld
    both = {"rollup": object(), "standing_head": object(), "culture": object()}
    for name, withheld in (
        ("rollup", {"standing_read", "character_read"}),
        ("standing_head", {"rollup_read", "character_read"}),
        ("culture", {"rollup_read", "standing_read"}),
    ):
        assert ToolRegistry(**{name: both[name]})._withheld & set(_LOOP_ONLY) == withheld
    assert not ToolRegistry(**both)._withheld & set(_LOOP_ONLY)


# ---- 4. tools resident on one occasion (DEC-256) ---------------------------

#: The voice members, as MEASURED (`scripts/measure_voice_tool_calls.py`,
#: 2026-10-01: 26 voice turns, 2026-09-17 .. 09-30). A literal on purpose: a
#: test that read the members back off `NativeTool.hot_on` would agree with any
#: declaration, including a dropped one. Changing this set means re-running the
#: measurement and putting its output in the PR, not editing the line to fit.
_VOICE_MEASURED = frozenset({
    "play_music", "set_music_volume", "stop_music", "music_status", "top_music",
})

#: Schema tokens (cl100k, `json.dumps(tool_spec)`) a voice turn carries on top
#: of a text turn's resident set, on every model call of the turn. Measured
#: 658 on 2026-10-01 (play 218, volume 118, top 158, status 84, stop 80); the
#: ceiling holds one more small verb (`skip_music` is 85). A new `hot_on`
#: member that does not fit re-sets this with its own measured count.
_VOICE_EXTRA_TOKEN_CEILING = 750


def test_every_declared_occasion_is_in_the_closed_vocabulary():
    from nora.agent.tools import NATIVE_TOOLS, TOOL_OCCASIONS

    stray = {name: sorted(spec.hot_on - TOOL_OCCASIONS)
             for name, spec in NATIVE_TOOLS.items() if spec.hot_on - TOOL_OCCASIONS}
    assert not stray
    # Positive control: an empty table would pass the line above too.
    assert any(spec.hot_on for spec in NATIVE_TOOLS.values())


def test_an_unknown_occasion_is_refused_not_read_as_text():
    from nora.agent.tools import hot_tools

    with pytest.raises(ValueError, match="unknown tool occasion"):
        hot_tools("spoken")
    with pytest.raises(ValueError, match="unknown tool occasion"):
        _surface(platform="discord", defer=True, occasion="spoken")


def test_the_voice_members_are_the_measured_ones():
    from nora.agent.tools import NATIVE_TOOLS

    declared = {name for name, spec in NATIVE_TOOLS.items() if "voice" in spec.hot_on}
    assert declared == _VOICE_MEASURED


def test_a_voice_turn_keeps_the_measured_tools_resident_and_a_text_turn_does_not():
    voice = _surface(platform="discord", defer=True, occasion="voice")
    text = _surface(platform="discord", defer=True)

    assert voice.resident >= _VOICE_MEASURED
    assert voice.resident == voice.admitted & (HOT_TOOLS | _VOICE_MEASURED)
    assert not _VOICE_MEASURED & voice.shelf.names()
    assert _names(voice.tools) >= _VOICE_MEASURED
    # The one music verb nobody called on a voice turn stays on the shelf.
    assert "skip_music" in voice.shelf.names()
    # The catalogue marks what is resident, so she does not load it again.
    marked = {line.split(" — ")[0][2:] for line in voice.catalogue.splitlines()
              if line.startswith("* ")}
    assert marked >= _VOICE_MEASURED

    assert not _VOICE_MEASURED & text.resident
    assert text.shelf.names() >= _VOICE_MEASURED


def test_a_voice_occasion_adds_nothing_where_the_scope_admits_no_music():
    """`hot_on` widens the resident set inside the scope, never past it: a
    voice occasion off Discord (none exists today) still gets no music verb."""
    surface = _surface(platform="signal", defer=True, occasion="voice")
    assert not _VOICE_MEASURED & (surface.admitted | surface.resident)


def _wire(surface: Any) -> tuple[list[str], str, dict]:
    return ([json.dumps(t.tool_spec, sort_keys=True) for t in surface.tools],
            surface.prompt_catalogue, surface.limits({"turns": 6}))


def test_a_text_turn_is_byte_identical_with_or_without_any_occasion_declared(monkeypatch):
    """What a text turn is handed (every tool schema in order, the catalogue
    and the loop limits) is the same bytes whether or not any tool declares
    `hot_on`. The comparison arm strips every declaration, which is the table
    as it was before DEC-256."""
    from nora.agent import tools as tools_module

    stripped = {name: dataclasses.replace(spec, hot_on=frozenset())
                for name, spec in tools_module.NATIVE_TOOLS.items()}
    for platform in ("discord", "signal"):
        declared = _wire(_surface(platform=platform, defer=True))
        with monkeypatch.context() as patch:
            patch.setattr(tools_module, "NATIVE_TOOLS", stripped)
            before = _wire(_surface(platform=platform, defer=True))
        assert declared == before, platform
    # Positive control: the voice turn is NOT the same bytes, or the
    # comparison above could not see a difference at all.
    assert (_wire(_surface(platform="discord", defer=True, occasion="voice"))
            != _wire(_surface(platform="discord", defer=True)))


def test_the_voice_occasion_schema_growth_stays_under_its_ceiling():
    enc = tiktoken.get_encoding("cl100k_base")
    voice = _surface(platform="discord", defer=True, occasion="voice")
    text = _surface(platform="discord", defer=True)

    def tokens(surface: Any) -> int:
        return sum(len(enc.encode(json.dumps(t.tool_spec))) for t in surface.tools)

    extra = tokens(voice) - tokens(text)
    assert 0 < extra <= _VOICE_EXTRA_TOKEN_CEILING, extra
