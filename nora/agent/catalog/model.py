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
    # `source_tier` is the sixth (E1/DEC-77). It answers "where does this tool's
    # answer COME from", so the model prefers a closer source over the open web
    # (a mall's hours go to `place_info`, a saved «work» place to
    # `saved_places`). Three ranks, closest first: "confirmed" (Nora's own
    # live/data tools — the weather feed, a bus, a route, a place, a fix),
    # "remembered" (what this household saved or told her), "web" (the open web,
    # last resort). `None` means the tool is not a fact SOURCE at all — an action
    # (`send_reply`, `remind`) or a pure function (`calculate`). The sourcing
    # rule itself is conduct.md's "prefer your own… open web, last resort"
    # sentence; tests pin the tiers (`test_the_confusable_retrieval_tools_
    # all_declare_a_source_tier`), so the per-tool declaration is what keeps
    # that rule true for each tool.
    source_tier: str | None = None
    # `cacheable` is the seventh. True only for a tool whose result carries no
    # room/person-disclosure decision baked into the MOMENT it ran —
    # `where_is`/`search_memory`/`about` all decide what they may say against
    # the room's LIVE roster, so replaying a cached answer days later would
    # skip that check entirely; none of this table's cacheable=True entries do.
    # Read by `ToolRegistry.cacheable_tools()` and nowhere else, the same
    # single-reader shape `terminal`/`time_shiftable` hold. Defaults False,
    # like every other declared field on this type — a new tool is
    # un-cacheable until its own entry says otherwise.
    cacheable: bool = False
    # `read_only` is the eighth. It answers "does calling this change anything
    # outside the turn": the Dreamer, a BACKGROUND research loop with nobody
    # waiting on it, may look and may not act. That line is a property of the
    # runtime rather than a setting, so it is derived from here and never
    # hand-listed (a hand-named list of act verbs goes stale as tools are
    # added).
    #
    # `terminal` is close and not sufficient — it marks turn-ENDING verbs
    # (`send_reply`, `send_gif`), while `remind`/`send_to` act without ending
    # anything.
    #
    # Defaults **False**, and the direction is the point: a tool that forgets
    # to declare itself is MISSING FROM THE DREAMER, never silently granted to
    # it. The same conservative direction `terminal` takes, for a worse failure
    # — a tool wrongly marked terminal truncates one turn; a tool wrongly
    # marked read-only hands an unattended loop a write.
    #
    # Read by `ToolRegistry.read_only_tools()`, which is the CEILING and not
    # the grant: each Dreamer adaptor declares its own narrower subset by name
    # and the runtime refuses any name outside this set.
    #
    # **One entry is arguably a lie; do not read it as precedent.**
    # `media_fetch` is `read_only=True` and writes a row — it persists the
    # vision model's description into `media_descriptions` via
    # `vision_cache.aput`. It is admitted because that write is an idempotent,
    # content-addressed CACHE of a pure function of the bytes: replaying it
    # changes no answer, discloses nothing new, and skipping it would only cost
    # a second vision call. That is a narrow exemption for a cache keyed on a
    # hash, NOT a general "writes are fine if they are cheap". A tool that
    # records that something HAPPENED — a call, a decision, a message — is an
    # act verb whatever it costs.
    read_only: bool = False
    # The contract triple — ninth, tenth and eleventh (ADR 0008, DEC-152). Where
    # `read_only` answers "may a background loop call this", these three answer
    # the question a FAILED call raises: what did it already do, may it be
    # replayed, and may the harness replay it. They are DECLARED, never derived
    # — a tool states what a failure of it means BEFORE one happens, because
    # after the timeout there is nothing left to derive it from.
    #
    # `side_effect_state` — what an interrupted call may have done to the world:
    #
    #   * `none` — cannot write outside the turn. Every `read_only=True` tool,
    #     `media_fetch` included: its content-addressed cache write is the
    #     narrow exemption the block above states, and replaying changes no
    #     answer.
    #   * `confirmed` — writes through an OBSERVABLE boundary: a store whose
    #     acknowledgement the harness actually reads back, so «did it apply» has
    #     an answer (`note_create`, `note_revise`, `remember`).
    #   * `unknown` — writes through a boundary whose outcome the runtime cannot
    #     observe. After a timeout the harness MUST assume the write may have
    #     applied (`nora.engagement.pipeline` — "Messages may ALREADY have gone
    #     out"); a timeout is `unknown`, never a guessed failure.
    #
    # `not_applied` is deliberately NOT in the declared vocabulary. ADR 0008 §1
    # lists it, and it is a RUNTIME OUTCOME — a call refused before it applied
    # (an R-2/R-4/R-6/R-18 veto) — not a property a tool can state about itself.
    # A tool cannot declare "I fail before applying"; only a particular call can
    # have done so. `SIDE_EFFECT_STATES` below is the closed declared set, and
    # the enforcement test reads it from there rather than restating it.
    #
    # `idempotency` is a property of the OPERATION (does replaying with the same
    # arguments produce the same effect); `retry` is a POLICY of the harness (may
    # it replay after an `unknown`). They are not one axis, which is why the
    # single `read_only` boolean cannot express either: `send_reply` is
    # `non_idempotent`/`never` because R-2's latch counts ATTEMPTS not successes
    # and there is no auto-retry, while `media_fetch` is `idempotent`/`safe`
    # because its cache is keyed on a hash of the bytes.
    #
    # `unsafe_without_key` has no entry today and exists so a write tool cannot
    # ship with an implicit retry policy: a tool declaring it must name the
    # idempotency key in its schema.
    #
    # All three default FAIL-CLOSED — `none`/`non_idempotent`/`never`. For the
    # two policy fields that is the conservative direction outright (a tool that
    # forgets to declare is never auto-replayed). `side_effect_state="none"` is
    # the PERMISSIVE value and is the default only because it is the answer for
    # most of the table; what stops a write verb inheriting it is the
    # enforcement test, which requires every entry to declare all three
    # EXPLICITLY rather than trusting the default.
    side_effect_state: str = "none"
    idempotency: str = "non_idempotent"
    retry: str = "never"
    # `output_contract` is the twelfth (ADR 0008 §3 as amended by ADR 0009,
    # DEC-153), and it is the first field on this table with a RUNTIME reader
    # rather than only a test: `nora.agent.ports.adapt` wraps the tool's
    # callable and checks every result against the contract named here BEFORE
    # Strands hands it to the model. A result that fails is replaced with
    # `{"status": "unavailable", "reason": "malformed_result"}` — R-27's "an
    # ERROR is never a PASS", made mechanical instead of conventional.
    #
    # The two values in use, and the rule for picking one:
    #
    #   * `"three_state"` — the tool's result carries the R-27 status axis.
    #     This is EVERY dict-returning tool on this table except the one below,
    #     including the writes (`send_to`, the events family, the music verbs),
    #     where the honesty axis matters most. `calculate` is on it too — it
    #     is a pure function, but it answers `ok`/`refused`, so it carries the
    #     axis.
    #   * `"messages"` — `load_transcript` alone (ADR 0009 §2). Both its returns
    #     are `_memory_result({"messages": ...})` with no `status` key at all,
    #     deliberately: the empty list IS the answer to a failed read. Under a
    #     contract requiring `status` every SUCCESSFUL call would be rewritten
    #     to `unavailable` and the recall ladder's middle rung would die
    #     silently. A tool that deliberately carries no status declares so.
    #
    # `None` is NOT VALIDATED and is the default. It is the answer for a tool
    # whose result is not a shaped object — `now` returns a formatted string,
    # `send_reply` returns a bool — and for `skills`, which is `plugin=True` and
    # has no body here to produce a result at all. Unlike `side_effect_state`,
    # the permissive default here costs a CHECK rather than granting a
    # permission, which is why this field is not required explicitly of every
    # entry the way the contract triple is; `tests/test_output_contracts.py`
    # is what stops a dict-returning tool inheriting `None` by accident.
    #
    # **The vocabulary lives in `nora.agent.ports`, not here**, deliberately
    # breaking this table's "declare it beside the entry" habit: `adapt()` READS
    # the contract at runtime, and `ports` is the lower module (`tools` imports
    # it, never the reverse), so a copy here would be a second definition. See
    # `OUTPUT_CONTRACTS` / `OUTPUT_STATUSES` there.
    output_contract: str | None = None
    # `level` (ADR 0015 §3, DEC-200): how much the description may carry. 1 is
    # what + when + what it returns (80 tokens), 2 adds one behaviour-changing
    # constraint (160), 3 adds a contract of statuses or refusals she must act
    # on differently (320), counted over the description Strands sends (the
    # docstring minus `Args:`). `None` is "not yet declared": the tools not yet
    # rewritten, on a shrink-only list in `tests/test_tool_surface_policy.py`.
    level: int | None = None
    # `link_inputs`: each entry names a parameter of THIS tool that may carry a
    # link another tool owns (`nora.agent.link_owners.LINK_OWNERS`);
    # `build_tool_specs`'s post-pass wraps `call` for every spec whose row
    # declares one, so a claimed link returns the owner's redirect without the
    # tool running at all, and an unclaimed one reaches the tool unchanged.
    # Empty tuple (the default) means "nothing here is ever another tool's
    # link" — a fetcher that takes a URL and forgets to declare it is
    # un-redirected, the same conservative direction `read_only` takes, because
    # the failure mode of forgetting is a page fetched instead of routed, not
    # the reverse.
    link_inputs: tuple[LinkInput, ...] = ()
    # `hot_on` (DEC-256): the turn OCCASIONS on which this tool's full schema
    # stays resident under `[llm] defer_tools`, on top of `HOT_TOOLS`. Empty
    # (the default) means the tool is resident only if `HOT_TOOLS` names it,
    # which is every turn's set and is untouched by this field. Each value
    # comes from `TOOL_OCCASIONS`, a closed vocabulary; the occasion of a turn
    # is derived once from its message (`turn_occasion`) by the loop that
    # builds the surface, never guessed by a tool.
    #
    # A MEASURED declaration, like `HOT_TOOLS` itself: a tool joins an
    # occasion when `scripts/measure_voice_tool_calls.py` (or its successor
    # for another occasion) shows it called on at least the share the
    # smallest `HOT_TOOLS` member holds (3.0%). It is paid in schema tokens on
    # every call of every such turn, and it gives those turns a tools array
    # the room's other turns do not share, so the prefix cache splits by
    # occasion; both costs are why it is not declared on intuition.
    # `ToolRegistry.surface()` is the one reader.
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
