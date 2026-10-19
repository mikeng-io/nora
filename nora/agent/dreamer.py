"""The Dreamer runtime: run ONE adaptor for ONE key, and return a three-state
outcome.

The Dreamer is an agent. It owns the loop, the tool ceiling, the budget and its
own doctrine, and it knows nothing about lexemes, exchanges or facts: everything
tier-specific arrives through `DreamAdaptor`. The path is fixed and the ORDER is
the design:

    adaptor declares an objective
      -> agent researches with READ-ONLY tools
      -> returns a structured result
      -> runtime VALIDATES the envelope
      -> runtime SCREENS the items          (R-18/R-28, before any tier type)
      -> adaptor PARSES them into its own values
      -> runtime STAMPS provenance
      -> adaptor COMMITS through its own sink

**The Dreamer holds no write tool by default, and the one exception is named.** A
`write_fact` tool is an act verb pointed at the database instead of at the group,
and it would defeat the screen: if the model writes directly there is no sink
left to screen at. The model proposes; code disposes. Every adaptor parses the
model's envelope into typed proposals and a SINK decides what actually lands.

The exception (DEC-162; DEC-133 (1) deprecated in part) is the proactive
adaptor's `remind`: ONE act verb, for ONE adaptor, admitted past the read-only
ceiling by an `AdaptorGrant` the composition root hands to the runtime BY NAME.
It is not a write tool in the sense above: the model's text is screened by
`dream_screen` inside the closure before `create`, and every other field of the
row (kind, owner, destination, payload) is stamped by CODE under a
`dream_adaptor` provenance the tool layer reads exactly as it reads `fire_turn`.
The decision IS the tool call; the envelope is bookkeeping. The Dreamer still
cannot speak: `deliverer=None`, and R-1b's two send paths are untouched, because
a row-driven occasion is a fifth occasion on the second path, not a third path.

**Why the runtime cannot use `create_agent`.** `create_agent` requires a
`ReplyGuard` (a deliverer, a set of terminal verbs, and the `tool_choice`
relaxation) and `create_model_provider` hardcodes `tool_choice="required"`, which
that module documents as load-bearing *for a turn that must end in `send_reply`*:
under `required` the model must call something on every pass, so a loop with no
terminal verb can never stop by itself, because the guard's
`BeforeModelCallEvent` hook is the only thing that ends a turn and it ends it by
observing a terminal call (DEC-21, DEC-32). The Dreamer has no terminal verb by
construction (the terminal ones ARE act verbs), so it needs its own guard
(`DreamGuard`) and its own stopping condition: the model declining to call
anything, under `tool_choice="auto"`, inside a wall clock.

**Validation.** `jsonschema` is not a dependency and one call site does not
justify adding one. So the RUNTIME validates the ENVELOPE (parses as JSON, is an
object, carries the adaptor's declared key, is a list under a size cap, every
element is a mapping) and the ADAPTOR's `accept` does the typed parse, dropping a
malformed item silently rather than raising, as `_parse_observations` /
`_parse_item` do.

**R-27 at this layer.** A model response that will not parse is `unavailable`
(«this batch was never actually read»), never `nothing_found` («there was
nothing in it»), because the loop must not advance a watermark past a batch
nobody read. The outcome type carries that guarantee now that the adaptor
contract returns a value instead of raising.

**The instruction digest is read PER RUN, never cached at construction**
(DEC-128). The corpus hot-reloads off a bind mount; a digest captured at
`__init__` keeps naming the corpus as it was at process start, and looks correct
the whole time, because wrongly-stamped rows are indistinguishable from right
ones. It is read once at the top of a run so a reload landing mid-run cannot
split one batch's rows across two digests, and it hashes only what was actually
SENT.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, get_args

from ..domain.errors import AccountNotRecordableError
from ..domain.models import TranscriptRow
from ..domain.ports import (
    DEFAULT_DREAM_STORE_TIMEOUT_SECONDS,
    DreamAdaptor,
    Proposal,
    RosterSource,
    VerbProvenance,
)
from ..domain.reasoning_strip import dreamer_path
from ..domain.turn_deadline import turn_deadline
from ..obs.metrics import Metrics
from ..obs.tracing import TRACE_KIND, annotate, span
from . import factory
from .dream_guard import DreamGuard
from .dream_screen import offence, screen
from .ports import ToolSpec, adapt
from .reasoning_strip import ReasoningStripWatch
from .registry import ToolRegistry
from .surface import DREAMER_LOOP_TOOLS

log = logging.getLogger("nora.dreamer")

_T = TypeVar("_T")

#: Which half of an adaptor's tick a store timeout happened in: the label
#: vocabulary for `nora_dream_store_timeout_total{phase}`, DERIVED off the
#: `Literal` rather than hand-copied into call sites and a help string. A typo in
#: a bare literal creates a NEW series rather than an error, and the old one
#: silently goes flat.
StorePhase = Literal["inputs", "commit"]
STORE_PHASES: frozenset[str] = frozenset(get_args(StorePhase))

# The envelope's bounds. The text is REJECT-shaped and the items are DROP-shaped,
# deliberately: a response 20x the size of any legal one is evidence the model was
# doing something other than answering, so nothing in it is trustworthy; a
# response of the right shape carrying too many items is an ordinary over-eager
# batch, and keeping the first N of it beats discarding a room's whole tick.
MAX_ENVELOPE_CHARS = 200_000
MAX_ITEMS = 64

# Tool-result caps. `MAX_ENVELOPE_CHARS` bounds the OUTGOING envelope only; a tool
# result re-entering the loop untruncated could spend the run's whole token
# budget on context. `DreamGuard` enforces the cap in its after-tool hook and
# appends a marker naming the cap, so a truncated result reads as truncated
# rather than as the whole answer. Memory verbs get the tighter number: their
# answers are summaries to reason over, not pages to quote.
MAX_TOOL_RESULT_CHARS = 8000
MAX_MEMORY_RESULT_CHARS = 4000
_TOOL_RESULT_CAPS: Mapping[str, int] = {
    "web_fetch": MAX_TOOL_RESULT_CHARS,
    "web_search": MAX_TOOL_RESULT_CHARS,
    "search_memory": MAX_MEMORY_RESULT_CHARS,
    # The unit variant is the same verb at a wider scope: same tighter number,
    # same reason.
    "search_memory_workspace": MAX_MEMORY_RESULT_CHARS,
    "about": MAX_MEMORY_RESULT_CHARS,
    "ask_memory": MAX_MEMORY_RESULT_CHARS,
    # The memory graph's one search verb (DEC-316), offered instead of the
    # Honcho verbs above while `[memory_index] enabled`: same number.
    "memory_search": MAX_MEMORY_RESULT_CHARS,
}

#: The same table under a public name, for the Cognition session's guard
#: (`nora.app.cognition_wiring`): one table, two readers, never a second copy.
TOOL_RESULT_CAPS: Mapping[str, int] = _TOOL_RESULT_CAPS

#: THE VERBS THE PER-PASS MEMORY CEILING COUNTS: every read that answers «what is
#: already known about this person», whichever store it goes to.
#: `tests/unit/test_dream_memory_budget.py` pins the set.
#:
#: `lookup_person` is in, though it looks like the one that could STARVE a pass (a
#: note involving three people cannot spend a ration of two looking each of them
#: up). It cannot, structurally: **`lookup_person` and the `<people>` block read
#: the same room through the same roster.** The block is built from `Room(platform,
#: conversation_id, roster_source, persons).members()` at the composition root;
#: `RegistryToolSource.build` hands the tools a `Room` on the same four values, and
#: `lookup_person` is `view.find(name)` over `room.scope()`, which the port
#: documents as «identical to `members()` when the roster is readable». So it can
#: name nobody the block did not already render; a handle the index does not carry
#: comes back `not_found`. Its only margin over the rendered list is a fuzzier
#: match on the SAME names. Leaving it out would leave a free verb answering the
#: question the prompt already answers.
#:
#: `note_read` is OUT: it reads a note the prompt INDEXED rather than a store the
#: prompt summarised, and it is the verb the index deliberately sends her to for a
#: body.
#:
#: NOT `_TOOL_RESULT_CAPS`' keys, which answers a different question about the
#: same verbs (how big may one answer be, not how many answers may a pass have)
#: and includes `search_memory`, the room-scoped sibling no unit-keyed adaptor
#: holds.
#:
#: `memory_search` (DEC-316) is the graph surface's memory verb, offered INSTEAD
#: of `about`, `ask_memory` and `search_memory_workspace` while `[memory_index]
#: enabled`.
#: The ration holds every memory verb of both surfaces, so it binds whichever one
#: a deployment registers.
MEMORY_TOOLS: frozenset[str] = frozenset({
    "about", "ask_memory", "search_memory_workspace", "lookup_person",
    "memory_search",
})

DreamStatus = Literal["ok", "nothing_found", "unavailable"]


@dataclass(frozen=True)
class AdaptorBudget:
    """One adaptor's ceiling. PER ADAPTOR, never one global number: proving a
    fact is worth more model calls than glossing a word, and a single ceiling
    would either starve the first or overpay for the second.

    **`timeout_seconds` is not optional.** Unlike a tag turn's timeout, a hung
    Dreamer call is QUIET: `dream_loop` marks the key `running` and skips it on
    every subsequent tick (`nora_dream_skipped_total{reason="in_flight"}`) without
    ever raising, so the exponential backoff, which only engages on an exception,
    never fires and a hung provider is skipped forever rather than backed off. The
    wall clock wraps the WHOLE loop, the way `nora/engagement/stages/invoke.py`
    and `nora/app/fire.py` each wrap theirs.
    """

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float
    #: **How many MEMORY reads one pass may make, across every memory verb
    #: together** (`MEMORY_TOOLS`), enforced by `DreamGuard`. The guard is built
    #: per sample, so its ledger resets every sample.
    #:
    #: **PER SAMPLE, so an adaptor with `AdaptorSchedule.samples = N` may make
    #: N x this per FIRE.** That is the grain of the three token caps (each is one
    #: model call's limit), and «per pass» reads as «per fire»: setting 2 on a
    #: 5-sample adaptor allows 10. Inert for the only adaptor that samples, which
    #: holds no tools at all.
    #:
    #: It sits beside the three token caps because it is the same KIND of number:
    #: what one run of one adaptor may spend. It is not a per-tool budget; those
    #: live with the tool (`NativeTool.budget`, DEC-10) and bound each verb on its
    #: own, which a turn asking four verbs the same question gets around.
    #:
    #: **Defaulted, so a ceiling exists by omission rather than by
    #: configuration.** 2 is the number `evals/corpora/proactive_decide.yaml`'s
    #: `d11` grades at (one read the objective requires before any `remind`, plus
    #: one). It is a STARTING POINT, not a measurement. Raise it in
    #: `[dreamer.budgets.<adaptor>] max_memory_calls` where a pass genuinely needs
    #: more.
    max_memory_calls: int = 2
    #: **The per-adaptor delta over the per-tool call ceilings**
    #: (`[dreamer.budgets.<adaptor>.tool_calls]`): tool name to how many calls ONE
    #: pass may make, applied by `_tool_budgets` OVER `NativeTool.budget` and over
    #: `[llm.budgets]`.
    #:
    #: The per-tool budget TABLE lives with the tool (`NativeTool.budget`,
    #: DEC-10). This is the per-adaptor override OF that table, the same
    #: relationship `[llm.budgets]` has to it for a tag turn, except that
    #: `[llm.budgets]` is deployment-wide and this is one adaptor's.
    #:
    #: Empty by omission: an adaptor that says nothing runs on the table's
    #: ceilings as `[llm.budgets]` adjusts them.
    tool_calls: Mapping[str, int] = field(default_factory=dict)
    #: **How many tool calls one pass may make, over EVERY verb together.**
    #: `max_memory_calls`' shape and grain (per sample, enforced by `DreamGuard`,
    #: answered `unavailable` past it) over the whole grant rather than the memory
    #: cluster: an adaptor granted every read verb is bounded by how much it reads,
    #: not by which verbs. `None`, the default, is no pass-wide ceiling.
    max_tool_calls: int | None = None


@dataclass(frozen=True)
class AdaptorSchedule:
    """One adaptor's own clock.

    A NAME-KEYED MAPPING value, like `AdaptorBudget`, and deliberately NOT a field
    on the `DreamAdaptor` protocol: a defaulted non-method member on a
    `@runtime_checkable` protocol fails `isinstance` for every adaptor that does
    not declare it. An adaptor with no entry in the mapping keeps its unscheduled
    behaviour (a watermark-bearing adaptor the shared `[language]` schedule:
    `lag > safety_lag`, `quiet_seconds`, `max_age_seconds`; a watermark-free one
    every tick), exactly as an adaptor with no budget entry gets `default_budget`.

    `min_interval_seconds` is the floor between runs, read two ways. A
    watermark-BEARING adaptor fires when there is unread backlog (`lag > 0`) AND
    the watermark row's `updated_at` is at least this old. A watermark-FREE
    adaptor has no cursor to age, so it fires on its SLOT alone,
    `floor((now - phase_offset) / min_interval_seconds)`, no backlog required,
    which is what lets a time-driven cron fire in a silent room. `batch_limit`
    replaces the shared cap for that adaptor's `record.since` read. `max_items`
    overrides the runtime's envelope cap (`MAX_ITEMS`) for that adaptor: the
    runtime DROPS past the cap while the watermark still advances, so the excess
    would be lost with no error and no re-run. `None` keeps the shared default.

    `phase_offset_seconds` shifts a watermark-free adaptor's slot boundary.
    Separating PERIODS is not separating PHASES: `dream_loop` dispatches adaptors
    as CONCURRENT tasks with no ordering, so adaptors with a data dependency (one
    writes a queue, the next drains it) given the same daily interval coincide
    every day, while the same interval at different offsets lets what one wrote in
    the morning be consumed the same afternoon rather than tomorrow. Ignored for a
    watermark-BEARING adaptor, whose fire time is set by its watermark's
    `updated_at` and has no slot to shift.
    """

    min_interval_seconds: float
    batch_limit: int
    max_items: int | None = None
    #: The EVIDENCE wake. Set, a watermark-bearing adaptor fires when
    #: `lag >= wake_batch` (enough new evidence) OR when `lag > 0` and the
    #: watermark row is at least `min_interval_seconds` old (stale, with something
    #: new). A key with nothing new never fires either way. `None` keeps the clock
    #: shape above, `lag > 0 and age >= min_interval_seconds`. Ignored for a
    #: watermark-FREE adaptor, which has no lag to count.
    wake_batch: int | None = None
    phase_offset_seconds: float = 0.0
    #: Model calls per fire, UNIONED. For an entry shape a model recognises in only
    #: a fraction p of samples at temperature 0.7, while per-sample invention is
    #: ~0, sampling exploits the temperature already paid for: the union of N
    #: samples recognises it with probability 1 - (1 - p)^N and stays empty on a
    #: negative batch. Duplicates across samples collapse in the adaptor's own
    #: staging key and create gate. A sample that is unreadable is counted and
    #: skipped; the run is `unavailable` only when EVERY sample was.
    #:
    #: **Samples run in SEQUENCE, so the run's worst case is N x the budget's
    #: timeout.** They are not gathered: a background pass nobody waits on buys
    #: nothing from parallelism except N concurrent calls at the gateway, and
    #: `dream_loop` already holds a per-`(adaptor, key)` `running` set, so a long
    #: run is skipped by the next tick rather than piling up behind itself.
    samples: int = 1

    #: On first run for a scope the inherit-source never covered, start at the
    #: RECORD HEAD rather than at 0.
    #:
    #: `seed_watermarks` inherits a new adaptor's cursor from another's, which
    #: covers a rename and a new adaptor over scopes the source knows. It does NOT
    #: cover a scope the source never saw: there the adaptor starts at 0 and
    #: re-reads the room from the beginning, `batch_limit` rows at a time, one
    #: model call per batch. For an adaptor whose output is a single evolving
    #: head, that writes accounts from slices of the room's PAST, each installed as
    #: the head that claims to say what the room is NOW, which corrupts the one
    #: signal its revision chain carries rather than merely costing calls.
    #:
    #: **Default `False`, because the right answer is per adaptor.** An extractor
    #: that appends idempotently may legitimately want the whole backlog; an
    #: adaptor whose output is a single evolving HEAD wants only what the room has
    #: been saying lately. Only the second kind sets this.
    seed_at_head: bool = False

    def __post_init__(self) -> None:
        if self.samples < 1:
            raise ValueError(f"samples must be >= 1, got {self.samples}")
        if self.wake_batch is not None and self.wake_batch < 1:
            raise ValueError(f"wake_batch must be >= 1, got {self.wake_batch}")


@dataclass(frozen=True)
class DreamOutcome:
    """What one run did, in R-27's three states.

    `unavailable` is never absent and never silently a zero: the Dreamer has
    nobody waiting on it, so a run that failed and a run that found nothing
    look identical from outside unless the difference is carried here.

    `stop_reason` is Strands' own, passed through. When one of the three caps binds,
    Strands raises nothing and logs nothing: `AgentResult.stop_reason` is the ONLY
    channel that says a loop was cut off rather than finished (R-23). A runtime
    that dropped it would report a budget-exhausted loop exactly the way it
    reports a healthy quiet one.
    """

    status: DreamStatus
    written: int
    instruction_sha256: str
    stop_reason: str | None = None


@dataclass(frozen=True)
class HomeRoom:
    """The room a home-room grant STANDS IN (`policy.toml`'s `home_room = true`).

    Built by the composition root from `Policy.home_rooms()` and handed to
    `RegistryToolSource`; the tool source never reads policy itself. Every field
    is what `ToolRegistry.build` needs to build the room-scoped tools exactly as a
    tag would: `conversation_id` and `conversation_type` for the bound
    conversation, `platform` for the roster source and the reminder row's
    destination, `location_trusted` from the room's own `location` flag (DEC-74).

    **WHICH room that is depends on the adaptor's `key_kind`, and this type serves
    both** (`RegistryToolSource.build`). A UNIT-keyed adaptor has no room of its
    own, so the operator CHOOSES one per unit in `policy.toml`; that choice is the
    disclosure decision `UNIT_SAFE_TOOLS` records. A SCOPE-keyed adaptor's key
    already IS a conversation, so its room is an IDENTITY rather than a choice: the
    composition root builds one of these per policy conversation id and the tool
    source looks it up by key. `unit` is which social unit the room belongs to
    either way: the map's key for the first, the room's own `Conversation.unit`
    for the second.
    """

    unit: str
    platform: str
    conversation_id: str
    conversation_type: str = "group"
    location_trusted: bool = False


@dataclass(frozen=True)
class AdaptorGrant:
    """What ONE named adaptor holds PAST the Dreamer's defaults.

    A NAME-KEYED MAPPING value like `AdaptorBudget` and `AdaptorSchedule`, for the
    reason those are: a defaulted non-method member on the `@runtime_checkable`
    `DreamAdaptor` protocol fails `isinstance` for every adaptor that does not
    declare it. An adaptor with no entry gets `AdaptorGrant()`: no act verbs, no
    home room.

    `home_room` says the adaptor's `Room` is built from a REAL room (a real roster
    source, `self_ids` and `location_trusted`) rather than from the empty one, and
    that `UNIT_SAFE_TOOLS` does not apply to it.

    **Which room, and how the disclosure question is answered, differs by
    `key_kind`** (`RegistryToolSource.build`):

    - **Unit-keyed.** A unit has no room, so one is CHOSEN. The refusal
      `UNIT_SAFE_TOOLS` records («binding one to an arbitrary room of that unit
      would be a disclosure decision nobody has made») is answered by the
      operator naming the room in `policy.toml`.
    - **Scope-keyed.** The key already IS a conversation, so there is nothing
      arbitrary to bind and nothing for the operator to choose: the grant resolves
      the adaptor's OWN room, the one whose whole message batch the runtime
      already handed it. The disclosure argument above does not apply because its
      premise, an arbitrary room, is absent.

    An adaptor with no grant still gets the closed, empty-room shape in both
    cases. This is a widening for adaptors that ASK, never a default.
    """

    home_room: bool = False


def culture_grant() -> AdaptorGrant:
    """The culture adaptor's grant: its own room, and NO act verb.

    `home_room=True` on a SCOPE-keyed adaptor resolves the adaptor's own
    conversation rather than a chosen one (`AdaptorGrant.home_room`,
    `RegistryToolSource.build`). Without it every room-scoped read the adaptor
    declares (`ask_memory`, `about`, `group_members`, `lookup_person`,
    `search_memory`) answers `unavailable` off the empty `Room`, so the grant is
    the difference between a declared tool set and a reachable one.

    The culture pass writes one row through its own store and speaks to nobody;
    the read-only ceiling refuses every act verb.

    A function rather than a module-level constant (DEC-13): the composition root
    hands the SAME object to the runtime and the startup check.
    """
    return AdaptorGrant(home_room=True)


def cognition_grant() -> AdaptorGrant:
    """The Cognition adaptor's grant: a real room, and NO act verb. Its write, pace
    and act verbs are not registry tools at all: the session holds them as its own
    closures (`nora.app.cognition_session`), so nothing here widens the read-only
    ceiling. It is SCOPE-resolved (`RegistryToolSource.build` with the agenda
    item's room as the key), so lineage and the roster's disclosure guard are the
    room's own. A function, for `culture_grant`'s reason (DEC-13)."""
    return AdaptorGrant(home_room=True)


class DreamToolSource(Protocol):
    """The tools an adaptor may hold, resolved by NAME.

    A documented structural type rather than a `nora.domain.ports` Protocol, the
    same reason `nora.app.runtime._InstructionVersions` and the observers'
    `_ObjectiveSource` are: a shape with one consumer does not earn a shared
    contract suite.

    It exists because building a tool is not free of context (`load_transcript`
    needs a store and a conversation, `about` needs Honcho, `web_search` needs the
    provider) and the composition root is the only place that holds all of them.
    The runtime asks for names and gets objects; it never learns what is behind
    them.

    **`read_only()` is the CEILING and `build` must enforce it.** An adaptor
    declares tool names because `nora.language` may not import `nora.agent`, which
    means nothing about the declaration itself stops it naming `send_reply`.
    `verify_tool_subsets` below is the startup refusal; `DreamerRuntime.run`
    re-checks per run so a subset that changed under a hot-reload cannot slip
    through as a quiet grant.

    `grant` is the ONE allowance past that ceiling, threaded from the runtime's
    name-keyed mapping so `build` and the two checks read the same value;
    `adaptor` is the run's provenance, stamped onto the tools as `dream_adaptor`
    the way the fire path stamps `fire_turn`.
    """

    def read_only(self) -> frozenset[str]: ...

    def build(
        self, *, key: str, names: frozenset[str], unit: str | None = None,
        adaptor: str = "", grant: AdaptorGrant | None = None,
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
    ) -> list[Any]: ...

    # R-99: `DreamGuard` needs the same accepted-argument table `ReplyGuard` reads
    # off `ToolRegistry.tool_param_names()`, or a Dreamer pass is exempt from the
    # rule a tag turn holds: a native tool built by `build_tool_specs` is exactly
    # as reachable from either loop, and `media_fetch(file=...)`'s silent drop is
    # exactly as live here. THE FULL registry table, like `read_only()` above, not
    # one adaptor's own granted subset: `verify_tool_subsets` reads `read_only()`
    # this way for the identical reason, a ceiling is checked against the whole
    # surface, never against what one call happened to ask for.
    def tool_param_names(self) -> Mapping[str, frozenset[str]]: ...


# The read-only tools a UNIT-keyed adaptor may hold, and the reason the set is
# exactly this.
#
# Every ROOM-scoped read tool stays refused here: `load_transcript` reads one
# room's record, `group_members`/`lookup_person`/`about`/`ask_memory`/
# `search_memory` all answer against one room's live roster, and
# `recall_lookups`/`media_fetch` replay one room's own calls. A unit has no single
# room to bind them to, and binding one to an arbitrary room of that unit would be
# a disclosure decision (R-31) nobody has made.
#
# The UNIT-scoped read tools are here because the key path is SUPPLIED: a
# unit-keyed adaptor's key IS the unit, and `DreamerRuntime.run` hands it through
# `RegistryToolSource.build(unit=...)` to `build_tool_specs`, where `_unit()`
# returns it directly instead of deriving it from the room. A tool that resolved
# its unit through the ROOM (`unit_for(room.platform, conversation_id)`) would
# never work for a unit-keyed adaptor: `RegistryToolSource.build` hands it a `Room`
# with no platform and the unit in the conversation slot, so policy could never
# name it and every call would answer `unavailable`. A tier read tool for a
# unit-keyed adaptor needs a key path that does not route through the room at all,
# because the Dreamer's key already IS the unit and must be SUPPLIED rather than
# derived.
#
# SCOPE-keyed adaptors get `unit=None`, `_unit()` falls back to the resolver, and
# the resolver reads the real room. The supplied value only ever WINS when a
# caller supplies one, and only the Dreamer does.
#
# An adaptor whose `AdaptorGrant.home_room` is set is EXEMPT from this set: the
# home room is the disclosure decision the first paragraph says nobody has made,
# and it is made per unit in `policy.toml`. Every other unit-keyed adaptor stays
# bound to it.
#
# Hand-listed HERE and pinned by a test that measures it against
# `read_only_tools()`, so a read tool added later is UNCLASSIFIED and turns that
# test red rather than silently joining or silently missing. That is the same
# structure `ACT_VERBS` uses in `tests/unit/test_tool_registry.py`.
UNIT_SAFE_TOOLS: frozenset[str] = frozenset({
    "web_search", "web_fetch", "now", "calculate", "weather", "version",
    # `x_read` and `x_search` (DEC-201): read the open internet (public X content)
    # with no room state anywhere in the answer, the same shape as
    # `web_search`/`web_fetch`, and read-only at three layers besides (R-86).
    "x_read", "x_search",
    # `instagram_search`: free text in, Instagram accounts out, with no name
    # resolved against a roster and no Tier 0 check, so no room state in the
    # answer: the same shape as `web_search`. Being here makes it grantable, not
    # granted; whether Nora may look people up unprompted in a Dreamer or fire turn
    # is an operator decision.
    "instagram_search",
    # Nora's OWN generated-image library: reads her library across every
    # conversation (the store records platform/conversation as provenance, but a
    # lookup is a global search of her own creations, never conversation-scoped
    # like `load_transcript`). That is its default scope. Its `room` scope
    # (DEC-251) reads the BOUND room only, so a run with a home room reads that
    # room and a run with none answers `unavailable`.
    "media_search",
    # Admitted with the supplied key path: the note tier's two read verbs, which
    # resolve their unit from the SUPPLIED key rather than from a room a
    # unit-keyed adaptor does not have, and the memory search variant, which
    # resolves NO unit at all.
    #
    # That last one is deployment-scoped, not unit-scoped: it reads across every
    # unit at once. It passes because this gate has two axes, read vs act and
    # room-scoped vs unit-safe, with no axis for scope ABOVE a unit. Being in this
    # set is what lets a DREAMER adaptor call it, which is intended; a TAG turn is
    # kept from it at the catalog instead (`ToolScope`'s Dreamer-only gate). The
    # missing axis here is still missing.
    "note_read", "note_search",
    # `memo_read` (DEC-306) resolves its unit the way the note reads do.
    "memo_read",
    "search_memory_workspace",
    # `memory_search` (DEC-316), the graph surface's memory verb: it reads the
    # memory index through a room's scope, so a unit-keyed run with no home room
    # answers `unavailable` (`media_search`'s shape above), never an empty answer.
    # Offered only while `[memory_index] enabled`.
    "memory_search",
    # `load_tools` reads neither a room nor a unit: it registers tools from THIS
    # run's own shelf onto THIS run's own agent, so it is as unit-safe as `now`.
    # Being here makes it grantable; whether a run holds it is
    # `ToolRegistry.surface`'s call, and it is only added when something was
    # deferred onto the shelf.
    "load_tools",
})


class _InstructionSource(Protocol):
    """`DreamerInstructions`, seen from the runtime: one objective name in, the
    assembled text and its digest out. Narrowed to the one method used so a
    test can supply an assembled pair without a corpus on disk."""

    def for_objective(self, name: str) -> Any: ...


class _ModelSource(Protocol):
    """One model provider per run, for the same reason there is one per tag:
    `tool_choice` is per-invocation state and two runs overlap."""

    def create(self) -> Any: ...


class AdaptorToolError(ValueError):
    """An adaptor asked for a tool outside the read-only ceiling, or for one that
    does not exist. Raised at STARTUP by `verify_tool_subsets` (R-5's
    refuse-to-start shape), because a background agent holding an act verb is not
    a condition to discover from a metric."""


def verify_tool_subsets(
    adaptors: Sequence[DreamAdaptor], *, tools: DreamToolSource,
    grants: Mapping[str, AdaptorGrant] | None = None,
    tool_calls: Mapping[str, Mapping[str, int]] | None = None,
) -> None:
    """Refuse to start if any adaptor's declared subset escapes the ceiling.

    The hard line (the Dreamer may look and may not act, save the one named
    allowance in `grants`) is enforced in two places on purpose, and neither is
    redundant. Here it is a startup refusal, which is where a wiring mistake
    belongs (`policy.toml`'s own precedent: she refuses to start rather than run
    an ambiguous policy). In `run` it is a per-run re-check, because the
    objective corpus hot-reloads and an adaptor that is rebuilt or swapped between
    ticks would otherwise be measured once, at a moment that has passed.

    `grants` is the SAME mapping the runtime holds (the composition root builds
    one and passes it to both), so the startup check measures exactly what the
    runtime enforces.

    `tool_calls` maps an adaptor name to its
    `[dreamer.budgets.<adaptor>.tool_calls]`, checked here and nowhere else,
    because a per-tool ceiling the operator raised for a tool the adaptor does not
    hold has no runtime symptom at all: the pass runs on the table's number and
    the config file says it does not.
    """
    ceiling = tools.read_only()
    for adaptor in adaptors:
        grant = (grants or {}).get(adaptor.name, AdaptorGrant())
        for message in tool_subset_faults(adaptor, ceiling=ceiling, grant=grant):
            raise AdaptorToolError(message)
        for message in tool_call_faults(
            adaptor, tool_calls=(tool_calls or {}).get(adaptor.name),
        ):
            raise AdaptorToolError(message)


def tool_subset_faults(
    adaptor: DreamAdaptor, *, ceiling: frozenset[str],
    grant: AdaptorGrant | None = None,
) -> tuple[str, ...]:
    """Every way this adaptor's declared subset is wrong, as messages.

    Two axes, and they are independent: a tool can be read-only and still be
    unusable by this adaptor.

    1. **read vs act.** Outside `read_only_tools()` is an act verb, and no
       background loop may hold one.
    2. **room-scoped vs unit-safe.** A UNIT-keyed adaptor gets a unit name as its
       `key`, which is not a conversation id. Every room-scoped read tool is bound
       to a conversation (`load_transcript` reads one room's record,
       `about`/`ask_memory`/`search_memory`/`group_members` answer against one
       room's live roster), so handing it a unit does not fail loudly, it returns
       EMPTY. `load_transcript` on an unknown conversation returns
       `{"messages": []}`, which is `unavailable` reported as `nothing_found`: the
       one substitution R-27 forbids. An adaptor whose grant names a HOME ROOM is
       exempt: it does have a room to bind them to, and the operator chose it.

    Split out of `verify_tool_subsets` so `DreamerRuntime.run` re-checks the
    identical rule rather than a second copy of it: the startup check and the
    per-run check disagreeing is worse than either being absent, because the
    startup one is the one people trust.
    """
    allowance = grant if grant is not None else AdaptorGrant()
    faults: list[str] = []
    declared = frozenset(adaptor.tools)
    escaped = declared - ceiling
    if escaped:
        faults.append(
            f"Dreamer adaptor {adaptor.name!r} declares tools outside the "
            f"read-only ceiling: {', '.join(sorted(escaped))}. The Dreamer "
            f"may look and may not act; a tool is grantable only if its "
            f"NativeTool row says read_only=True, or an AdaptorGrant names "
            f"it for this adaptor (39 D3)."
        )
    loop_only = declared & DREAMER_LOOP_TOOLS
    if loop_only:
        # A grant DERIVED from the read-only ceiling would swallow both tools
        # silently. They are the Cognition session's (`cognition_wiring.read_surface`,
        # which does not pass through this check); no DreamAdaptor holds them,
        # however its tools were derived.
        faults.append(
            f"Dreamer adaptor {adaptor.name!r} declares Cognition-only tools: "
            f"{', '.join(sorted(loop_only))}. {', '.join(sorted(DREAMER_LOOP_TOOLS))} "
            f"belong to the Cognition session's own surface, not to an adaptor."
        )
    if adaptor.key_kind == "unit" and not allowance.home_room:
        room_scoped = declared - UNIT_SAFE_TOOLS
        if room_scoped:
            faults.append(
                f"Dreamer adaptor {adaptor.name!r} is keyed on a UNIT and "
                f"declares room-scoped tools: {', '.join(sorted(room_scoped))}. "
                f"A unit is not a conversation, so these would answer "
                f"EMPTY rather than fail — «I could not check» reported as "
                f"«there was nothing». Unit-safe tools are: "
                f"{', '.join(sorted(UNIT_SAFE_TOOLS))}."
            )
    return tuple(faults)


def tool_call_faults(
    adaptor: DreamAdaptor, *, tool_calls: Mapping[str, int] | None = None,
) -> tuple[str, ...]:
    """Every per-tool ceiling this adaptor was given for a tool it does not hold, as
    messages.

    A SEPARATE function from `tool_subset_faults` rather than a third axis inside
    it, because the two read different things: that one measures the adaptor's own
    declaration against the ceiling, this one measures the OPERATOR's config
    against the adaptor's declaration. They also fail at different grains: a
    subset fault is a code mistake in the objective corpus, a ceiling fault is a
    typo in `config.toml`.

    Pure and returning messages, the same shape, so `verify_tool_subsets` is the
    only thing that raises.

    **Why a boot failure and not a warning.** `_tool_budgets` skips a key naming a
    tool that did not build, so the outcome is already SAFE: it cannot conjure
    permission for a tool the adaptor was never granted. What it cannot be is
    visible: the pass runs on the table's ceiling while `config.toml` records a
    raised one, and the only tell is a veto counter nobody is watching.
    `[dreamer.budgets.<adaptor>] max_memory_calls` takes the opposite posture for
    a misspelled ADAPTOR name (`budget_for` is deliberately lenient) because of
    what the mistake costs: there the run is bounded by numbers the operator did
    not choose, which is survivable; here the operator's own fix is inert.
    """
    if not tool_calls:
        return ()
    declared = frozenset(adaptor.tools)
    unheld = frozenset(tool_calls) - declared
    if not unheld:
        return ()
    return (
        f"Dreamer adaptor {adaptor.name!r} has per-tool call ceilings for "
        f"tools it does not declare: {', '.join(sorted(unheld))}. "
        f"[dreamer.budgets.{adaptor.name}.tool_calls] may only name a tool "
        f"in this adaptor's own subset ({', '.join(sorted(declared)) or 'none'}) "
        f"— a ceiling on a tool it never calls raises nothing and is read by "
        f"nothing, so it would leave the config saying a pass is bounded "
        f"differently than it is.",
    )


def _envelopes(raw: str, *, key: str):
    """Every VALID envelope in `raw`, in order: an object carrying `key` whose value
    is a list of mappings.

    **`raw_decode` at each `{`, not hand-rolled brace counting.** Counting braces
    with its own quote and escape tracking breaks on an unterminated quote in
    prose (`'no source for the 5" pipe'` is not exotic for a research pass) and on
    an unclosed `{`, either of which swallows the rest of the reply.
    `json.JSONDecoder.raw_decode` is the real parser; it gets strings, escapes and
    nesting right by construction, and a candidate that fails simply advances to
    the next `{`.

    **Validity is checked HERE rather than by the caller**, because a malformed
    object carrying the key would otherwise SHADOW a well-formed one after it:
    `{"verdicts": ["a string"]}` followed by a real envelope has to yield the real
    envelope, not `None`.
    """
    decoder = json.JSONDecoder()
    at = raw.find("{")
    while at != -1:
        try:
            data, end = decoder.raw_decode(raw, at)
        except (json.JSONDecodeError, ValueError, RecursionError):
            at = raw.find("{", at + 1)
            continue
        if (
            isinstance(data, dict)
            and isinstance(items := data.get(key), list)
            and all(isinstance(item, Mapping) for item in items)
        ):
            yield data
        at = raw.find("{", max(end, at + 1))


def parse_envelope(raw: str, *, key: str) -> tuple[Mapping[str, object], ...] | None:
    """The model's raw text -> the declared key's items, or `None` for any shape
    that is not exactly `{"<key>": [ {...}, ... ]}`.

    `None` means the batch was NOT READ: an empty body, truncated JSON and a wrong
    top-level key all land here, and the caller maps every one of them to
    `unavailable`. It is deliberately NOT the same value as an empty list, which
    is a well-formed «I looked and found nothing».

    **A PREAMBLE is not one of them.** The envelope need not be the FIRST thing in
    the reply: a model told to write only the JSON object still sometimes narrates
    before it, and `json.loads` over the whole string would then fail, report
    `unavailable` and hold the watermark, so a batch that WAS read is re-read
    forever while the counter says the model could not be reached. Discarding a
    readable answer because a sentence preceded it turns a `nothing_found` into an
    outage, which is R-27 upside down. A terminal tool call would be the better
    shape, but it is a change to every adaptor and to the guard that ends a
    Dreamer turn.

    The strictness that matters is kept: the envelope must be a JSON object
    carrying the declared key, whose value is a list of mappings. The objective
    still tells the model to write nothing else; a prompt asking for less than the
    parser accepts is the safe direction.

    Never raises: one malformed response must not take down a tick that other
    adaptors share.
    """
    if len(raw) > MAX_ENVELOPE_CHARS:
        log.warning(
            "dream envelope rejected: %d chars exceeds the %d cap",
            len(raw), MAX_ENVELOPE_CHARS,
        )
        return None
    # THE LAST valid envelope, not the first. Every objective prints the empty form
    # (`{"verdicts": []}`) as its nothing-found instruction, so a model that quotes
    # its instructions on the way to answering puts a VALID but empty envelope
    # ahead of its real one. Taking the first yields zero items, which is
    # `nothing_found`, which ADVANCES THE WATERMARK: real verdicts discarded and
    # the batch consumed permanently, with every counter green. On an objective
    # that prints a POPULATED example, the first would write the documentation's
    # own worked example as a real row. Last also matches the model's habit: it
    # narrates, then answers.
    envelopes = list(_envelopes(raw, key=key))
    if not envelopes:
        return None
    return tuple(envelopes[-1][key])


class RegistryToolSource:
    """`DreamToolSource` over the real `ToolRegistry`: the only place that turns an
    adaptor's tool NAMES into tool objects.

    Two guards, and they are independent on purpose:

    1. **The read-only ceiling.** Only a name in `read_only_tools()` is ever built,
       so an act verb cannot reach a background loop even if an adaptor asks for
       one and even if `verify_tool_subsets` was skipped.
    2. **An empty room.** The `Room` handed to `ToolRegistry.build` has NO roster
       source, so its view is `available=False` («I could not look») and any
       room-scoped tool that did slip through discloses nothing rather than
       answering from a room the Dreamer was never given. That is a DATA-level
       fail-closed guard behind the name-level one, because the name-level one is
       a list and lists go stale.

    A unit-keyed adaptor is additionally restricted to `UNIT_SAFE_TOOLS` (see that
    constant), because a unit has no room at all: `key` is not a conversation id
    there, and passing it as one would quietly ask a room-scoped tool about a
    conversation that does not exist.

    **Both guards have one named exception, and it is the same one.** An adaptor
    whose `AdaptorGrant` names a home room is built FROM that room: the real
    roster source for its platform, Nora's own `self_ids` so her account is
    skipped rather than counted `unnamed`, and `location_trusted` from the room's
    `location` flag, so `where_is` and its siblings run the disclosure guard
    exactly as on a tag. Everything about the build that is not the room is
    unchanged: `deliverer=None`, and the unit is still SUPPLIED. A home-room grant
    that resolves no room falls back to the closed shape above and says so, never
    a guessed room.

    **The grant resolves through TWO maps, one per `key_kind`, and the difference
    is what the key already names.** A unit-keyed adaptor's key is a unit NAME, so
    the room is looked up in `home_rooms`, the operator's choice per
    `policy.toml`'s `home_room = true`. A scope-keyed adaptor's key is a
    CONVERSATION id, so there is nothing to choose: the room is looked up in
    `scope_rooms` by that same key, which is why the lookup is an identity rather
    than a decision and why `UNIT_SAFE_TOOLS`' disclosure argument has no purchase
    on it (the adaptor is already running FOR that room and was already handed its
    whole message batch). `DreamerRuntime.run` supplies `unit=None` for a
    scope-keyed adaptor, so looking it up in `home_rooms[unit or ""]` would find
    nothing.
    """

    def __init__(
        self, *, registry: ToolRegistry, store: Any, persons: Any, honcho: Any,
        metrics: Metrics, ai_peer: str = "nora",
        home_rooms: Mapping[str, HomeRoom] | None = None,
        scope_rooms: Mapping[str, HomeRoom] | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._store = store
        self._persons = persons
        self._honcho = honcho
        self._metrics = metrics
        self._ai_peer = ai_peer
        # Unit -> home room, from `Policy.home_rooms()`. Empty for every
        # composition that names none.
        self._home_rooms = dict(home_rooms or {})
        # Conversation id -> that room, for a SCOPE-keyed adaptor's home-room
        # grant. The same value type as `home_rooms` because it answers the same
        # question (which room do the room-scoped verbs resolve through), and a
        # different MAP because the key is a different thing: a conversation id
        # the runtime already holds, not a unit name the operator had to pair with
        # a room. Keyed by every id form a conversation answers to
        # (`Conversation.ids`), so a room with an `also_ids` form still resolves;
        # empty for every composition that names none, which leaves the
        # scope-keyed build closed.
        self._scope_rooms = dict(scope_rooms or {})
        # Platform -> roster source, the same map `FirePath` holds: a home
        # room on a platform with no roster answers `available=False`, the
        # honest state, never an empty room.
        self._rosters = dict(rosters or {})
        self._self_ids = frozenset(self_ids)
        # (adaptor, unit) pairs already reported for the missing-home-room branch
        # below. The condition is a CONFIG state, true on every pass until
        # policy.toml changes, so an error per pass is a stream of identical lines
        # (and one Sentry issue per dream). The COUNTER carries the ongoing
        # reading; the log carries the news.
        self._home_room_reported: set[tuple[str, str]] = set()

    def read_only(self) -> frozenset[str]:
        return self._registry.read_only_tools()

    def tool_param_names(self) -> Mapping[str, frozenset[str]]:
        return self._registry.tool_param_names()

    def build(
        self, *, key: str, names: frozenset[str], unit: str | None = None,
        adaptor: str = "", grant: AdaptorGrant | None = None,
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
    ) -> list[Any]:
        from .room import Room

        allowance = grant if grant is not None else AdaptorGrant()
        # WHICH MAP follows from what `unit` says about the caller rather than from
        # a second parameter nobody could forget to pass: `DreamerRuntime.run`
        # supplies the unit for a unit-keyed adaptor and `None` for a scope-keyed
        # one, so `None` here IS «the key is a conversation». See the class
        # docstring for why the scope side is an identity and not a disclosure
        # decision.
        if not allowance.home_room:
            home = None
        elif unit is None:
            home = self._scope_rooms.get(key)
        else:
            home = self._home_rooms.get(unit)
        if allowance.home_room and home is None:
            # Loud, and CLOSED: the grant said «stand in a real room» and none
            # resolved. Building the room-scoped tools against a synthetic room
            # would answer «nobody is here» (`available=False`), which is why the
            # room is withheld, not faked.
            self._metrics.inc(
                "nora_dream_home_room_missing_total",
                adaptor=adaptor, unit=unit or "",
            )
            # Once per (adaptor, room-or-unit), not once per pass: the counter
            # above is the continuous reading, and repeating an unchanging config
            # fault at `error` on every dream buries the passes that DID change
            # something. The second half of the key is whatever failed to
            # resolve: the unit for a unit-keyed adaptor, the KEY for a
            # scope-keyed one, whose misses would otherwise all collapse onto `""`
            # and report the first broken room as though it were the only one.
            seen = (adaptor, unit if unit is not None else key)
            if seen not in self._home_room_reported:
                self._home_room_reported.add(seen)
                if unit is None:
                    log.error(
                        "dream adaptor %r is granted a home room but scope %r "
                        "is not a conversation in policy.toml; building "
                        "without a room", adaptor, key,
                    )
                else:
                    log.error(
                        "dream adaptor %r is granted a home room but unit "
                        "%r names none in policy.toml; building without a "
                        "room", adaptor, unit,
                    )
        wanted = frozenset(names) & self.read_only()
        if not wanted:
            return []
        if home is not None:
            room = Room(
                platform=home.platform, conversation_id=home.conversation_id,
                roster_source=self._rosters.get(home.platform),
                persons=self._persons,
                # No tagger: the run is owed to nobody. `Room.scope()`'s degraded
                # branch therefore narrows to nothing when the roster cannot be
                # read, and `where_is` answers `unavailable` there.
                self_ids=self._self_ids,
            )
        else:
            room = Room(
                platform="", conversation_id=key, roster_source=None,
                persons=self._persons,
            )
        return self._registry.surface(
            # The adaptor's ceiling, applied by the SAME scope every loop's tools
            # pass through.
            grant=wanted,
            store=self._store,
            # The HOME ROOM's conversation, not the key, when there is one: for a
            # unit-keyed adaptor the key is the unit NAME, and a reminder row bound
            # to it would die `failed_policy` at every fire.
            conversation_id=home.conversation_id if home is not None else key,
            conversation_type=home.conversation_type if home is not None else "group",
            persons=self._persons,
            honcho=self._honcho,
            # No deliverer, ever. Nothing the Dreamer holds speaks to anybody, and
            # the filter below cannot admit a tool that would; the absence is
            # stated here too so a `None` is not replaced by a deliverer.
            deliverer=None,
            metrics=self._metrics,
            room=room,
            # DEC-74's flag, threaded from the home room exactly as a tag threads
            # it from `Conversation.location`. `False` (the strict gate) for every
            # build with no home room.
            location_trusted=home.location_trusted if home is not None else False,
            # This run's PROVENANCE, read by `remind` the way it reads `fire_turn`:
            # under it the closure stamps the row in code instead of reading a
            # tagger it does not have.
            dream_adaptor=adaptor,
            # The key path: for a UNIT-keyed adaptor `key` IS the unit, so the
            # runtime SUPPLIES it and the unit-scoped verbs (the note reads; NOT
            # `search_memory_workspace`, which resolves no unit and reaches
            # Honcho's whole workspace) read it directly rather than deriving one
            # from the synthetic room above, where `unit_for("", key)` can only
            # answer `None`. `None` for a scope-keyed adaptor, whose key is a real
            # conversation id and whose tools resolve through the room: the EMPTY
            # room without a home-room grant, and the room the key names with one.
            supplied_unit=unit,
            # The rows the transcript reads return, for an adaptor that grounds on
            # what it loaded; `None` otherwise.
            on_rows=on_rows,
        ).tools


class DreamerRuntime:
    """One public entry point: `run`.

    Collaborators through `__init__` only, per the test-seam policy: no `*_fn`
    hooks, no `_for_test` parameters. `budgets` is a mapping from adaptor name to
    its ceiling; an adaptor with no entry gets `default_budget` rather than a
    `KeyError` or an unbounded loop, because an adaptor added later must be
    bounded by omission rather than unbounded by it.

    `schedules` is the same shape for the run's other per-adaptor number: an
    adaptor whose `AdaptorSchedule.max_items` is set gets that envelope cap
    instead of `MAX_ITEMS` (the cap is DROP-shaped while the watermark still
    advances, so a large extraction batch would lose every claim past the cap
    with no error and no re-run). An adaptor with no entry keeps the shared cap.
    """

    def __init__(
        self,
        *,
        models: _ModelSource,
        instructions: _InstructionSource,
        tools: DreamToolSource,
        budgets: Mapping[str, AdaptorBudget],
        default_budget: AdaptorBudget,
        retry: factory.RetryPolicy,
        metrics: Metrics,
        model_name: str,
        schedules: Mapping[str, AdaptorSchedule] | None = None,
        tool_budget_overrides: Mapping[str, int] | None = None,
        grants: Mapping[str, AdaptorGrant] | None = None,
        store_timeout_seconds: float = DEFAULT_DREAM_STORE_TIMEOUT_SECONDS,
        reasoning_strip_store: Any = None,
    ) -> None:
        self._models = models
        self._instructions = instructions
        self._tools = tools
        self._budgets = dict(budgets)
        self._default_budget = default_budget
        self._retry = retry
        self._metrics = metrics
        self._model_name = model_name
        # `| None` rather than `= {}`: a mutable default shared across instances is
        # a trap (ruff B006), and this mapping is only ever READ, so coalescing
        # here costs nothing.
        self._schedules = dict(schedules or {})
        # `[llm.budgets]`, the per-deployment delta over `NATIVE_TOOLS`. The tag path
        # applies it via `ToolRegistry(budget_overrides=cfg.llm.budgets).budgets()`;
        # `_tool_budgets` applies it here too, so one number in `config.toml` means
        # the same ceiling whichever loop is asking.
        #
        # A plain `Mapping[str, int]` through `__init__` rather than a config read:
        # `nora.agent` may not import `nora.config` (the layer contract), which is
        # the same reason `max_adjudications_per_tick` arrives as an int on the
        # adaptors.
        self._tool_budget_overrides = dict(tool_budget_overrides or {})
        # Name -> `AdaptorGrant`: the ONE allowance past the read-only ceiling and
        # past `UNIT_SAFE_TOOLS`, per adaptor, by name. The same mapping the
        # composition root hands `verify_tool_subsets`, so the startup check and
        # the per-run re-check below cannot disagree about who holds what. Empty by
        # omission: no act verbs for any adaptor.
        self._grants = dict(grants or {})
        # `[dreamer] store_timeout_seconds`: the per-CALL-SITE allowance, multiplied
        # by the adaptor's own declared `store_calls_per_tick`. A plain float
        # through `__init__` for the same reason `tool_budget_overrides` is one:
        # `nora.agent` may not import `nora.config`.
        self._store_timeout_seconds = store_timeout_seconds
        # `None` is the inert default (the eval harness builds two runtimes without
        # one). A tool-using run at `reasoning_effort = "low"` is the multi-turn
        # shape the transport strips reasoning from, so a runtime that holds the
        # watch reports it.
        self._reasoning_strip_store = reasoning_strip_store

    def budget_for(self, adaptor_name: str) -> AdaptorBudget:
        return self._budgets.get(adaptor_name, self._default_budget)

    def store_bound_for(self, adaptor: DreamAdaptor) -> float:
        """The wall clock `inputs` and `commit` each run under.

        `store_calls_per_tick` is a count of awaited collaborator CALL SITES,
        declared in the adaptor's own module and pinned there by
        `tests/architecture/test_dream_store_calls_are_declared.py`; the config
        value is the allowance PER SITE. A site inside a per-item loop runs once per
        item, so this is not a latency budget: it is the wall clock that turns
        «this store call never returns» into a counted `unavailable` instead of a
        `(adaptor, key)` that is never ticked again. `nora/config.py`'s
        `[standing] max_slot_hold_seconds` and its startup gate are the same shape
        one tier over.
        """
        return adaptor.store_calls_per_tick * self._store_timeout_seconds

    async def _bounded_store_call(
        self, *, adaptor: DreamAdaptor, key: str, phase: StorePhase,
        call: Callable[[], Coroutine[Any, Any, _T]],
    ) -> _T:
        """Run one adaptor store phase under `store_bound_for`, and COUNT a timeout
        rather than letting it read as an ordinary failure.

        Re-raises: the caller's own `except Exception` maps it to R-27's
        `unavailable` («nobody read this batch»), which already holds the
        watermark. The counter is what this adds:
        `nora_dream_run_total{status="unavailable"}` is also what a 429 and a prose
        reply produce, so `nora_dream_store_timeout_total` is the series that
        separates a wedged store from a bad afternoon at the provider, and `phase`
        says which half.

        **`asyncio.timeout` and `cm.expired()`, not a bare `except TimeoutError`.**
        `socket.timeout IS TimeoutError`, so an aiohttp read timeout, an asyncpg
        `command_timeout` or any other collaborator's OWN clock raises the same
        class this handler catches. Counting those as «the runtime cut it off»
        would put a false reading on the one series an operator would page on.
        `expired()` is the only thing that can tell the two apart; an inner
        `TimeoutError` is re-raised untouched and reaches `unavailable` by the
        ordinary path.

        **`call` is a FACTORY, not a coroutine.** Built eagerly, a coroutine whose
        bound could not be computed (an adaptor object that is not a
        `DreamAdaptor`) is never awaited: a `RuntimeWarning` and an `unavailable`
        whose real cause is two frames away from what it says.

        **What this does NOT bound.** `asyncio.timeout` cancels and then WAITS for
        the cancellation to complete, so a collaborator that swallows
        `CancelledError`, or a driver whose own cancel needs the database that just
        stopped answering, can outlive this, and a SYNCHRONOUS blocking call inside
        an `async def` is not interrupted at all, because it never yields. The
        claim is «the unit is released when the phase can be cancelled», not «a
        hang cannot happen».
        """
        bound = self.store_bound_for(adaptor)
        try:
            async with asyncio.timeout(bound) as cm:
                return await call()
        except TimeoutError:
            if not cm.expired():
                # The COLLABORATOR's own clock, not ours. Untouched, uncounted
                # on this series, and `unavailable` by the ordinary path.
                raise
            self._metrics.inc(
                "nora_dream_store_timeout_total",
                adaptor=adaptor.name, phase=phase,
            )
            log.error(
                "dream %s timed out after %.0fs (adaptor=%s key=%s) — a store "
                "call did not return; the unit is released rather than held",
                phase, bound, adaptor.name, key,
            )
            raise

    def grant_for(self, adaptor_name: str) -> AdaptorGrant:
        """This adaptor's allowance: `AdaptorGrant()` (nothing) for an adaptor with
        no entry, never a `KeyError` inside a background tick."""
        return self._grants.get(adaptor_name, AdaptorGrant())

    def samples_for(self, adaptor_name: str) -> int:
        """Model calls per fire: `AdaptorSchedule.samples`, 1 for an adaptor with no
        schedule entry."""
        sched = self._schedules.get(adaptor_name)
        return sched.samples if sched is not None else 1

    def batch_limit_for(self, adaptor_name: str) -> int | None:
        """How many rows this adaptor is handed per fire: its schedule's
        `batch_limit`, `None` for an adaptor with no schedule entry.

        The third of the per-adaptor numbers this runtime hands back
        (`samples_for`, `max_items_for`): the value is per-ADAPTOR and lives here,
        so a caller that needs it should ask rather than keep a copy.
        `nora/app/runtime.py`'s `_dream_adaptor_once` reads the same field off the
        same mapping. A caller that drives adaptors with its own hardcoded limit
        runs a tier present but REDUCED relative to the deployment.
        """
        sched = self._schedules.get(adaptor_name)
        return sched.batch_limit if sched is not None else None

    def max_items_for(self, adaptor_name: str) -> int:
        """This adaptor's envelope item cap: its schedule's `max_items` where set,
        else the runtime-wide `MAX_ITEMS`."""
        sched = self._schedules.get(adaptor_name)
        if sched is not None and sched.max_items is not None:
            return sched.max_items
        return MAX_ITEMS

    async def render_input(self, *, adaptor: DreamAdaptor, key: str, batch: Sequence[Any]) -> str:
        """Offline inspection through the same instruction store and adaptor.

        Calls no model, builds no agent and commits nothing. The caller must
        discard the subject afterwards: `inputs` stages the adaptor's read.
        """
        instructions = self._instructions.for_objective(adaptor.objective)
        prompt = await self._bounded_store_call(
            adaptor=adaptor, key=key, phase="inputs",
            call=lambda: adaptor.inputs(key=key, batch=batch),
        )
        return instructions.text + "\n\n" + prompt

    async def run(
        self, *, adaptor: DreamAdaptor, key: str, batch: Sequence[TranscriptRow]
    ) -> DreamOutcome:
        """One adaptor, one key, one bounded read-only loop, traced.

        The root span names the adaptor and annotates the outcome (`_run_body`
        holds the body; see it for all the reasons a failure returns `unavailable`
        rather than raising), so a pass's decision (remind / decline /
        nothing_found) is readable without expanding the model call.
        """
        with span(
            f"dream:{adaptor.name}",
            **{TRACE_KIND: "generation",
               "nora.dreamer.adaptor": adaptor.name,
               "nora.dreamer.key": key,
               "nora.dreamer.objective": adaptor.objective},
        ):
            outcome = await self._run_body(
                adaptor=adaptor, key=key, batch=batch,
            )
            annotate(**{"nora.outcome": outcome.status})
            return outcome

    async def _run_body(
        self, *, adaptor: DreamAdaptor, key: str, batch: Sequence[TranscriptRow]
    ) -> DreamOutcome:
        """One adaptor, one key, one bounded read-only loop.

        Every failure path returns `unavailable` and is COUNTED. Nothing here
        raises into the caller: `dream_loop`'s per-key try/except drives an
        exponential backoff against an external model provider, and an ordinary
        «the model returned prose» must not look like an outage to it. The
        exception is the tool-ceiling re-check, which is a wiring fault rather
        than a runtime one and is loud.
        """
        started = time.perf_counter()
        budget = self.budget_for(adaptor.name)
        sha = ""
        try:
            # PER RUN, and read before anything else so a mid-run reload cannot
            # split one batch's rows across two digests (DEC-128).
            instructions = self._instructions.for_objective(adaptor.objective)
            sha = instructions.sha256

            grant = self.grant_for(adaptor.name)
            faults = tool_subset_faults(
                adaptor, ceiling=self._tools.read_only(), grant=grant,
            )
            if faults:
                # Loud, and `unavailable` rather than a raise: a background loop must
                # not take its own clock down, but this is a wiring fault and reads
                # as one. The IDENTICAL rule the startup check runs (one function,
                # two call sites), because a per-run check that had drifted from
                # the startup one would be worse than none.
                for fault in faults:
                    log.error("dream run refused: %s", fault)
                return self._finish(
                    adaptor, "unavailable", 0, sha, None, started,
                )

            # `inputs` BEFORE `build`: a blank prompt is the fire condition, and
            # building a whole tool registry (with its roster source and, for a
            # home-room grant on a unit that names none, a daily ERROR line) for a
            # run that is about to be skipped is cost with nothing to show for it.
            # Nothing below the blank check reads `tools`, and nothing in `inputs`
            # does either: an adaptor's inputs are its stores, never its tools.
            #
            # BOUNDED: `inputs` is a read against this adaptor's own stores and the
            # engine sets no `command_timeout`, so an unbounded read on a
            # connection hanging mid-query would leave `dream_loop` holding
            # `<adaptor>:<key>` in `running` forever.
            prompt = await self._bounded_store_call(
                adaptor=adaptor, key=key, phase="inputs",
                call=lambda: adaptor.inputs(key=key, batch=batch),
            )
            if not prompt.strip():
                # **THE FIRE CONDITION, and it is general.** A blank `inputs` means
                # the adaptor has nothing to reason about, and calling a model with
                # nothing to reason about is a research loop spent on an empty
                # prompt.
                #
                # It matters most for an adaptor with `uses_watermark = False`,
                # which gets no lag/quiet/age conditions at all and would otherwise
                # fire on EVERY tick. The port's own docstring says such an adaptor
                # "must carry its own fire condition", and this is where the
                # runtime lets it: a daily slot plus a blank `inputs`.
                #
                # `commit(key, ())` is still called, so the decay contract holds: a
                # watermarked adaptor over a quiet room renders a blank prompt too,
                # and that path is where a tier's time-driven work lives, recording
                # its wakes on an empty commit whatever the model did or did not
                # say. Reported `nothing_found` («I looked and there was nothing»)
                # because that is what happened; `unavailable` would hold a
                # watermark over a batch that was genuinely empty.
                written = await self._bounded_store_call(
                    adaptor=adaptor, key=key, phase="commit",
                    call=lambda: adaptor.commit(
                        key=key, proposals=(), now_ms=int(time.time() * 1000),
                    ),
                )
                return self._finish(
                    adaptor, "nothing_found", written, sha, None, started,
                )
            # WHAT THE PASS LOADED: the transcript rows its tools returned, across
            # every sample, handed to the adaptor before `accept` so a citation of
            # a loaded row is a citation of a row the pass SAW. Optional on the
            # protocol: an adaptor with no `saw` grounds on its prompt alone.
            saw = getattr(adaptor, "saw", None)
            loaded: list[TranscriptRow] = []
            # WHICH ROOM the read tools resolve through. A scope-keyed adaptor's key
            # IS its room; an adaptor keyed on something else (a Character entity)
            # names the room its reads stand in through `tool_room`, and one with
            # no `tool_room` (the unit's and Nora's sessions, which hold no read
            # tool) resolves through its own key, which names no room, so every
            # room-scoped verb is closed to it.
            tool_room = getattr(adaptor, "tool_room", None)
            tools = self._tools.build(
                key=(tool_room(key) or key) if tool_room is not None else key,
                names=frozenset(adaptor.tools),
                # A unit-keyed adaptor's key IS the unit: supply it so the
                # unit-scoped verbs resolve it directly instead of asking
                # `unit_for` to name a room that does not exist. A scope-keyed
                # adaptor supplies nothing and its tools resolve through the room.
                unit=key if adaptor.key_kind == "unit" else None,
                adaptor=adaptor.name, grant=grant,
                # Only for an adaptor that grounds on what it loaded (`saw`).
                **({"on_rows": loaded.extend} if saw is not None else {}),
            )
            # THE ADAPTOR'S OWN WRITE VERBS (`character_revise`): typed closures
            # that VALIDATE a write as the model makes it and answer a refusal it
            # can act on, the shape the Cognition session's verbs have
            # (`nora.app.cognition_session`). They stage; they never touch a store.
            # What they staged comes back through `accept`, the runtime stamps it,
            # and `commit` writes it, so «the model proposes, code disposes» and
            # the provenance are exactly what an envelope adaptor gets.
            verbs = getattr(adaptor, "verbs", None)
            own = (
                [_as_tool(self._screened(adaptor.name, v))
                 for v in self._bind_verbs(adaptor, key, verbs, sha)]
                if verbs is not None else []
            )
            # The BUDGETS, not the guard. A `DreamGuard` owns a `CallLedger`, and a
            # ledger is SPENT STATE: built once per run, the second sample would
            # start with the first's tool calls already counted against it, so an
            # adaptor with N samples would get a fraction of its declared budget
            # per sample. The guard is built inside `_sample` for that reason.
            # Inert for an adaptor that holds no tools.
            tool_budgets = _tool_budgets(
                tools, adaptor, self._tool_budget_overrides,
                # This adaptor's own delta, last and narrowest. It rides on `budget`
                # rather than on `__init__` because it is one adaptor's number, and
                # `budget_for` is the one place that answers «what may this adaptor
                # spend»; a second name-keyed mapping on the runtime would be a
                # second place for the same question to be answered differently.
                budget.tool_calls,
            )
            for verb in own:
                tool_budgets[_tool_name(verb)] = VERB_CALLS
            tools = [*tools, *own]
        except Exception:
            log.exception(
                "dream run failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            return self._finish(adaptor, "unavailable", 0, sha, None, started)

        # THE MODEL CALL(S): `samples` of them, unioned (`AdaptorSchedule.samples`).
        # Each sample is its own agent, its own timeout and its own envelope parse;
        # one unreadable sample is counted and skipped, and the run is
        # `unavailable` only when none was readable, because «nobody read this
        # batch» is what holds the watermark and one good read is a read.
        # `stop_reason` reported is the last readable sample's.
        samples = self.samples_for(adaptor.name)
        union: list[Mapping[str, object]] = []
        readable = 0
        stop_reason: str | None = None
        for _ in range(samples):
            sample_items, sample_stop = await self._sample(
                adaptor=adaptor, key=key, instructions_text=instructions.text,
                sha=sha, tools=tools, tool_budgets=tool_budgets,
                prompt=prompt, budget=budget,
            )
            if sample_items is None:
                self._metrics.inc(
                    "nora_dream_sample_total", adaptor=adaptor.name,
                    status="unavailable",
                )
                if stop_reason is None:
                    stop_reason = sample_stop
                continue
            self._metrics.inc(
                "nora_dream_sample_total", adaptor=adaptor.name, status="ok",
            )
            readable += 1
            stop_reason = sample_stop
            union.extend(sample_items)
        if readable == 0:
            return self._finish(
                adaptor, "unavailable", 0, sha, stop_reason, started,
            )
        items = _dedupe_items(union)
        # THE CAP IS PER RUN, and with sampling that is per UNION rather than per
        # model answer. Identical items collapse first (above), so N samples that
        # agree cost one slot; N samples that each invent something different are
        # exactly the case a cap is for.
        max_items = self.max_items_for(adaptor.name)
        if len(items) > max_items:
            # Capped, never silently: a cap nobody counts reads as «we wrote
            # everything it found».
            dropped = len(items) - max_items
            log.warning(
                "dream run returned %d items, keeping %d (adaptor=%s key=%s)",
                len(items), max_items, adaptor.name, key,
            )
            # ONE increment carrying the count, not one call per item: the envelope
            # cap is 200k characters, so a pathological response can hold tens of
            # thousands of items and a per-item loop would spend the tick inside
            # prometheus rather than on the drop it is reporting.
            self._metrics.inc(
                "nora_dream_screened_total", float(dropped),
                adaptor=adaptor.name, reason="cap",
            )
            items = items[:max_items]

        kept, _rejected = screen(
            adaptor=adaptor.name, items=items, metrics=self._metrics,
        )
        if items and not kept:
            # **TOTAL REJECTION, named rather than inferred.** An objective that asks
            # the model to echo a row id back gets every item dropped by
            # `dream_screen` (`_UUID` matches any hyphenated uuid), so the tick
            # spends a model call, `accept` receives `()`, `commit` writes nothing,
            # and the run reports `ok`. The per-item `nora_dream_screened_total`
            # fires, but a run's item count is not on that series, so «was that ALL
            # of them» cannot be asked of it.
            #
            # The STATUS deliberately does not move. `ok` here is R-27 read
            # correctly: `nothing_found` is the claim «there was nothing», and a
            # run whose model returned items is not entitled to make it (the same
            # rule that stops a bus catalogue's empty answer becoming 「冇呢條線」).
            # Collapsing the two would also make a WORKING screen indistinguishable
            # from a silent model, which is the reading an operator needs most on
            # the day an adaptor starts echoing an identifier. So the distinction is
            # a counter, structurally zero while every adaptor keeps identifiers out
            # of its envelope.
            self._metrics.inc(
                "nora_dream_empty_after_screen_total", adaptor=adaptor.name,
            )

        try:
            if saw is not None and loaded:
                saw(key=key, rows=tuple(loaded))
            payloads = await adaptor.accept(key=key, items=kept)
            now_ms = int(time.time() * 1000)
            proposals = tuple(
                Proposal(
                    payload=payload,
                    instruction_sha256=sha,
                    model=self._model_name,
                    # The run that produced it: the handle a later `supersede` names
                    # when it retires this row. Derived from the tick rather than
                    # minted at random so a row can be tied back to a trace without
                    # a lookup table.
                    established_by=f"dream:{adaptor.name}:{key}:{now_ms}",
                )
                for payload in payloads
            )
            # Called even when `proposals` is empty: an empty `commit` is R-27's
            # `nothing_found` and the sink may act on it (a sink may record its
            # wakes on one, because decay is time-driven). Skipping the call would
            # drop decay silently.
            # BOUNDED, and the cancellation this can deliver is a DELIBERATE trade:
            # a `commit` cut off mid-sequence leaves the tier's own live state (a
            # PENDING candidate row, a note retired but not yet woken) which the
            # next tick re-selects, because a store fault is `unavailable` and never
            # a verdict. A commit that never returns leaves the UNIT dead instead,
            # and nothing re-selects that.
            #
            # **The trade holds only where the tier ORDERED its writes for it, and
            # that is a property of each adaptor rather than of this line.** An
            # adaptor that stamps its daily clock FIRST would record the day as done
            # with nothing written after a cancelled commit, so the stamp goes last.
            # An adaptor has to answer the same question rather than inherit an
            # answer.
            written = await self._bounded_store_call(
                adaptor=adaptor, key=key, phase="commit",
                call=lambda: adaptor.commit(
                    key=key, proposals=proposals, now_ms=now_ms,
                ),
            )
        except AccountNotRecordableError as unsettled:
            # A Character session that settled nothing: expected, the adaptor has
            # already said so and counted it, and the status is the same
            # `unavailable` with the watermark HELD. A WARNING and no stack trace,
            # so a model that keeps failing to settle is not read as a fault in the
            # code.
            log.warning(
                "dream session unsettled; holding the batch (adaptor=%s key=%s): %s",
                adaptor.name, key, unsettled,
            )
            return self._finish(
                adaptor, "unavailable", 0, sha, stop_reason, started,
            )
        except Exception:
            # ACCEPT OR COMMIT: the try spans both, and either can fault (a fault in
            # `accept` is the adaptor refusing its own model's result,
            # `DreamAdaptor.accept`'s narrow raise). Either way the status below is
            # `unavailable` and the watermark is HELD, which is the point of
            # catching here at all.
            log.exception(
                "dream accept/commit failed (adaptor=%s key=%s)",
                adaptor.name, key,
            )
            return self._finish(
                adaptor, "unavailable", 0, sha, stop_reason, started,
            )

        # `ok` vs `nothing_found` is about what the MODEL returned, not about how
        # many rows survived: a batch whose every item was screened out, or whose
        # every verdict was `not_covered`, was still a batch that was read.
        # Collapsing them would make a working screen look like a silent model. A
        # verb adaptor returns no items; what it staged is its answer, and a verb
        # that WRITES when called (Lessons) answers through what its `commit` says
        # landed.
        status: DreamStatus = "ok" if items or proposals or written else "nothing_found"
        return self._finish(adaptor, status, written, sha, stop_reason, started)

    async def _sample(
        self, *, adaptor: DreamAdaptor, key: str, instructions_text: str,
        sha: str, tools: list[Any], tool_budgets: Mapping[str, int],
        prompt: str, budget: AdaptorBudget,
    ) -> tuple[tuple[Mapping[str, object], ...] | None, str | None]:
        """ONE model call: build a guard, build the agent, invoke it under the
        budget, read the envelope. Returns `(items, stop_reason)`; `items is None`
        means this sample was not readable (timeout, exception, or no envelope),
        counted by the caller, never raised.

        **The guard is built HERE, once per sample**, because it owns a
        `CallLedger` and a ledger is spent state: a shared one would let the first
        sample's tool calls count against the second's budget. The TOKEN budget
        (`budget`) is per sample too: it is the model's own limit for one call, and
        N samples of a bounded call is what sampling means. **`max_memory_calls`
        inherits that**, which makes it a per-PASS ceiling rather than a
        per-adaptor-lifetime one: a guard built here cannot carry yesterday's
        reads, or the previous unit's, or the previous sample's.
        """
        guard = DreamGuard(
            adaptor=adaptor.name,
            budgets=dict(tool_budgets),
            metrics=self._metrics,
            result_caps=_TOOL_RESULT_CAPS,
            memory_tools=MEMORY_TOOLS,
            memory_budget=budget.max_memory_calls,
            pass_budget=budget.max_tool_calls,
            # R-99: the same table `deliver.build`'s callers thread into
            # `ReplyGuard`, read off the SAME `DreamToolSource` this method already
            # holds, never a second `ToolRegistry` instance.
            param_names=self._tools.tool_param_names(),
        )
        # One per SAMPLE, beside the guard and for the same reason: it holds this
        # sample's own call counts, and a shared one would fold N samples into a
        # single row whose denominator no longer describes any loop that ran.
        # `path` carries the adaptor by name; `tag_id` stays None because a
        # Dreamer run has no tag, and the column is nullable so the absence can be
        # recorded as an absence.
        watch = ReasoningStripWatch(
            path=dreamer_path(adaptor.name),
            store=self._reasoning_strip_store,
            metrics=self._metrics,
        )
        try:
            agent = factory.create_dream_agent(
                model=self._models.create(),
                system_prompt=instructions_text,
                tools=tools,
                guard=guard,
                reasoning_watch=watch,
                trace_attributes={
                    "dreamer.adaptor": adaptor.name,
                    "dreamer.key": key,
                    "dreamer.objective": adaptor.objective,
                    "dreamer.instruction_sha256": sha,
                },
                retry=self._retry,
                agent_name=f"dreamer:{adaptor.name}",
            )
            # A room-scoped pass can replay `media_fetch`; its describer must see
            # THIS pass's ceiling, not the `[llm]` loop it was built from.
            with turn_deadline(budget.timeout_seconds):
                result = await asyncio.wait_for(
                    factory.invoke(
                        agent, prompt, invocation_state={},
                        limits=factory.build_limits(
                            output_tokens=budget.output_tokens,
                            turns=budget.turns,
                            total_tokens=budget.total_tokens,
                        ),
                    ),
                    timeout=budget.timeout_seconds,
                )
        except TimeoutError:
            log.error(
                "dream run timed out after %.0fs (adaptor=%s key=%s)",
                budget.timeout_seconds, adaptor.name, key,
            )
            await watch.flush()
            return None, "timeout"
        except Exception:
            log.exception(
                "dream run failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            await watch.flush()
            return None, None
        # On every exit, including the two above: a sample that timed out is still
        # a sample whose loop made model calls, and those are the calls least
        # visible from anywhere else. Written out three times rather than hidden
        # in a `finally`, because awaiting in a `finally` on a cancelled task
        # re-raises out of the cleanup path, and a record of something that has
        # already happened must never change how a run ends. `flush` is
        # idempotent.
        #
        # BOUNDED INSIDE `flush`, for `_bounded_store_call`'s reason one method
        # over: a dream run holds its unit and a wedged store does not raise. This
        # one is NOT inside `store_bound_for`'s arithmetic: it is not an adaptor
        # store phase and carries its own, smaller, bound
        # (`ReasoningStripWatch.DEFAULT_WRITE_TIMEOUT_SECONDS`), because one INSERT
        # is not a render's worth of reads.
        await watch.flush()
        stop_reason = getattr(result, "stop_reason", None)
        if stop_reason is not None:
            self._metrics.inc(
                "nora_dream_stop_reason_total",
                adaptor=adaptor.name, reason=str(stop_reason),
            )
        if getattr(adaptor, "verbs", None) is not None:
            # A VERB ADAPTOR'S ANSWER IS ITS CALLS, not an envelope: its writes were
            # validated and staged as they were made, and `accept` reads what was
            # staged. A loop that RETURNED is a readable sample; one that timed out
            # or raised is not (above), and whether it settled what it was asked to
            # is the adaptor's to judge in `accept`.
            return (), stop_reason
        raw = factory.final_text(result)
        items = parse_envelope(raw, key=adaptor.envelope_key)
        if items is None:
            log.warning(
                "dream run produced no readable envelope (adaptor=%s key=%s, "
                "%d chars)", adaptor.name, key, len(raw),
            )
            return None, stop_reason
        return items, stop_reason

    def _bind_verbs(
        self, adaptor: DreamAdaptor, key: str, verbs: Callable[..., Any], sha: str,
    ) -> Sequence[Callable[..., Any]]:
        """`adaptor.verbs(key=key)`, plus `provenance` when the adaptor's `verbs`
        takes it: a verb that WRITES when called (Lessons) stamps what a `Proposal`
        would carry, so its rows name the same run and the same instruction digest
        an envelope adaptor's do. A staging verb (Character) takes none."""
        if "provenance" not in inspect.signature(verbs).parameters:
            bound: Sequence[Callable[..., Any]] = verbs(key=key)
            return bound
        provenance = VerbProvenance(
            instruction_sha256=sha, model=self._model_name,
            established_by=f"dream:{adaptor.name}:{key}:{int(time.time() * 1000)}",
        )
        written: Sequence[Callable[..., Any]] = verbs(key=key, provenance=provenance)
        return written

    def _screened(
        self, adaptor: str, verb: Callable[..., Any],
    ) -> Callable[..., Any]:
        """`verb`, refusing any call whose arguments carry an identifier or a
        coordinate (R-18/R-28) BEFORE it runs: the same screen, the same reasons and
        the same counter an envelope item meets (`dream_screen`), because a verb's
        text reaches a store that renders into later prompts exactly as an item's
        does. Refused, not dropped: the model reads the reason and can write the
        account without it."""
        @functools.wraps(verb)
        async def screened(*args: Any, **kwargs: Any) -> dict[str, object]:
            found = offence(kwargs)
            if found is not None:
                reason, path = found
                self._metrics.inc(
                    "nora_dream_screened_total", adaptor=adaptor, reason=reason,
                )
                log.warning(
                    "dream_screened: adaptor=%s verb=%s reason=%s path=%s",
                    adaptor, verb.__name__, reason, path,
                )
                return {
                    "status": "refused", "reason": reason,
                    "detail": f"`{path}` carries an {reason}; nothing that "
                              "identifies or locates a person may be written. "
                              "Write it without that",
                }
            result: dict[str, object] = await verb(*args, **kwargs)
            return result
        return screened

    def _finish(
        self, adaptor: DreamAdaptor, status: DreamStatus, written: int,
        sha: str, stop_reason: str | None, started: float,
    ) -> DreamOutcome:
        self._metrics.inc(
            "nora_dream_run_total", adaptor=adaptor.name, status=status,
        )
        if written:
            self._metrics.inc(
                "nora_dream_written_total", adaptor=adaptor.name,
            )
        self._metrics.observe(
            "nora_dream_run_seconds", time.perf_counter() - started,
            adaptor=adaptor.name,
        )
        return DreamOutcome(
            status=status, written=written, instruction_sha256=sha,
            stop_reason=stop_reason,
        )


def _dedupe_items(
    items: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """The union of N samples with byte-identical items collapsed, first occurrence
    kept in order. Two samples that phrase one arrangement differently are NOT
    collapsed here; that is the adaptor's job (the staging key and the create gate
    decide what is the same occasion), and a runtime that guessed would be deciding
    a tier's identity rule for it."""
    seen: set[str] = set()
    out: list[Mapping[str, object]] = []
    for item in items:
        try:
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            fingerprint = repr(item)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(item)
    return tuple(out)


#: Calls ONE pass may make of each of an adaptor's own write verbs. A write that
#: is refused answers a reason the model can act on, so it needs room to correct
#: and call again; a session writes one entity, so six is a ceiling on a loop that
#: keeps being refused, not on the work.
VERB_CALLS = 6


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "tool_name", None) or getattr(tool, "__name__", "") or "")


def _as_tool(verb: Callable[..., Any]) -> Any:
    """An adaptor's write verb (a typed closure) as a Strands tool, through
    `nora.agent.ports.adapt`, the one place a `ToolSpec` becomes one (ADR 0015:
    the schema comes from the closure's signature and docstring)."""
    return adapt(ToolSpec(
        name=verb.__name__, description="", schema={}, call=verb, budget=VERB_CALLS,
    ))


def _tool_budgets(
    tools: list[Any],
    adaptor: DreamAdaptor,
    overrides: Mapping[str, int] | None = None,
    adaptor_overrides: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """The per-tool call ceiling `DreamGuard` enforces under R-7.

    Read off the BUILT tools rather than off the adaptor's declared names, so a
    name the tool source declined to build is absent from `budgets` and is
    therefore vetoed by default-deny rather than admitted with a ceiling it cannot
    spend. `adaptor` is named only in the log line, which makes a mismatch
    findable rather than merely safe.

    `overrides` is `[llm.budgets]`, applied OVER the table exactly as
    `ToolRegistry.budgets()` applies it for a tag's turn. Applied only to a tool
    that BUILT: an override naming a tool this adaptor does not hold must not
    conjure a budget entry, because a name in `budgets` is what turns
    default-deny into permission.

    `adaptor_overrides` is THIS adaptor's `[dreamer.budgets.<adaptor>.tool_calls]`,
    applied last and winning over both: three layers, widest first (the tool's own
    table, the deployment's delta, this adaptor's). Applied before `overrides` it
    would be silently overwritten by a deployment-wide number the operator set for
    the tag path.

    It is subject to the same built-only rule, and a key that names a tool this
    adaptor does not hold has ALREADY refused the boot (`tool_call_faults`); this
    skip is the second half of that, for the window where a hot-reloaded objective
    drops a tool the config still names.
    """
    from .tools import NATIVE_TOOLS

    budgets: dict[str, int] = {}
    for tool in tools:
        name = str(
            getattr(tool, "tool_name", None) or getattr(tool, "__name__", "") or ""
        )
        spec = NATIVE_TOOLS.get(name)
        if spec is None:
            log.warning(
                "dream tool %r has no NATIVE_TOOLS row; it will be vetoed "
                "(adaptor=%s)", name, adaptor.name,
            )
            continue
        budgets[name] = (adaptor_overrides or {}).get(
            name, (overrides or {}).get(name, spec.budget),
        )
    return budgets
