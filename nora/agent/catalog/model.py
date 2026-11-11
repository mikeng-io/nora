from __future__ import annotations

from dataclasses import dataclass

from nora.agent.link_owners import LinkInput


@dataclass(frozen=True)
class NativeTool:
    """What is declared about a tool once, in one place.

    `budget` and `terminal` live together because they are two answers to the
    same question — how a turn is allowed to spend itself — and because the
    failure mode this table exists to prevent is a tool being added to one list
    and forgotten in the other. `budget` feeds `ReplyGuard` through
    `ToolRegistry.budgets()` (DEC-10); `terminal` feeds the guard's turn-ending
    hook through `ToolRegistry.terminal_tools()` (DEC-32). Both are read from
    here and nowhere else, so a tool that exists has a ceiling AND a stated
    answer to "does calling this end the turn", or it does not build. The
    routing trigger (when to reach for a tool) lives in each tool's schema
    docstring, not here.

    `terminal` defaults to False because almost nothing is terminal and because
    the wrong default truncates a turn rather than merely lengthening one.
    Marking another tool terminal is a one-line edit here rather than an edit
    to `guards.py`.

    `time_shiftable` is the third, and it is the one field here that is not a
    boolean: `None` means the tool has no time dimension, `False` means its
    answer describes this instant and decays, `True` means it accepts a target
    time. See `nora.agent.ports.ToolSpec` for the full argument. The answer to
    "does this answer go stale" is declared here, once, and
    `tests/unit/test_tool_registry.py::test_every_tool_declares_whether_it_can_be_asked_about_another_moment`
    pins the values.

    `plugin` is the fourth (DEC-48, DEC-50). It marks a tool this table gives a
    BUDGET but does NOT itself build or describe: the `skills` activation tool
    is registered by `strands.vended_plugins.skills.AgentSkills` (wired in
    `nora.agent.factory`), not by `build_tool_specs`, and it advertises itself
    through the plugin's own `<available_skills>` block. So a `plugin=True`
    entry is present ONLY so R-7's default-deny admits the tool (`ReplyGuard`
    reads `NATIVE_TOOL_BUDGETS`); it is filtered out of `tool_names()` (the
    doctrine catalogue gate) and is never built or described by this registry,
    because the offline/eval compositions never wire the plugin — promising a
    tool that composition does not have is what the filter prevents. The budget
    is the one thing that MUST be shared; the description is the one thing that
    must not.
    """

    budget: int
    terminal: bool = False
    time_shiftable: bool | None = None
    plugin: bool = False
    # `source_tier` is the sixth (E1/DEC-77). It answers "where does this tool's answer
    # COME from", so the model prefers a closer source over the open web (a mall's hours
    # go to `place_info`, a saved «work» place to `saved_places`). Three ranks, closest
    source_tier: str | None = None
    # `cacheable` is the seventh. True only for a tool whose result carries no
    # room/person-disclosure decision baked into the MOMENT it ran —
    # `where_is`/`search_memory`/`about` all decide what they may say against the room's
    cacheable: bool = False
    # `read_only` is the eighth. It answers "does calling this change anything outside the
    # turn": the Dreamer, a BACKGROUND research loop with nobody waiting on it, may look
    # and may not act. That line is a property of the runtime rather than a setting, so it
    read_only: bool = False
    # The contract triple — ninth, tenth and eleventh (ADR 0008, DEC-152). Where
    # `read_only` answers "may a background loop call this", these three answer the
    # question a FAILED call raises: what did it already do, may it be replayed, and may
    side_effect_state: str = "none"
    idempotency: str = "non_idempotent"
    retry: str = "never"
    # `output_contract` is the twelfth (ADR 0008 §3 as amended by ADR 0009, DEC-153), and
    # it is the first field on this table with a RUNTIME reader rather than only a test:
    # `nora.agent.ports.adapt` wraps the tool's callable and checks every result against
    output_contract: str | None = None
    # `level` (ADR 0015 §3, DEC-200): how much the description may carry. 1 is what + when
    # + what it returns (80 tokens), 2 adds one behaviour-changing constraint (160), 3
    # adds a contract of statuses or refusals she must act on differently (320), counted
    level: int | None = None
    # `link_inputs`: each entry names a parameter of THIS tool that may carry a link
    # another tool owns (`nora.agent.link_owners.LINK_OWNERS`); `build_tool_specs`'s post-
    # pass wraps `call` for every spec whose row declares one, so a claimed link returns
    link_inputs: tuple[LinkInput, ...] = ()
    # `hot_on` (DEC-256): the turn OCCASIONS on which this tool's full schema stays
    # resident under `[llm] defer_tools`, on top of `HOT_TOOLS`. Empty (the default) means
    # the tool is resident only if `HOT_TOOLS` names it, which is every turn's set and is
    hot_on: frozenset[str] = frozenset()


SIDE_EFFECT_STATES: frozenset[str] = frozenset({"none", "confirmed", "unknown"})

IDEMPOTENCY_VALUES: frozenset[str] = frozenset({"idempotent", "non_idempotent"})

RETRY_POLICIES: frozenset[str] = frozenset({"safe", "unsafe_without_key", "never"})

TOOL_OCCASIONS: frozenset[str] = frozenset({"voice"})


def turn_occasion(*, voice: bool) -> str | None:
    """The occasion of one turn, from the flag its message carries.

    One derivation for every loop that calls `ToolRegistry.surface()`, so the
    tag path and the fire path cannot disagree about what a spoken turn is.
    `None` is a text turn: no occasion, and the resident set is `HOT_TOOLS`."""
    return "voice" if voice else None
