"""The Dreamer runtime: run ONE adaptor for ONE key, and return a three-state
outcome.

The Dreamer is an agent (`docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md`).
It owns the loop, the tool ceiling, the budget and its own doctrine, and it
knows nothing about lexemes, exchanges or facts — everything tier-specific
arrives through `DreamAdaptor`. The path is fixed and the ORDER is the design:

    adaptor declares an objective
      -> agent researches with READ-ONLY tools
      -> returns a structured result
      -> runtime VALIDATES the envelope
      -> runtime SCREENS the items          (R-18/R-28, before any tier type)
      -> adaptor PARSES them into its own values
      -> runtime STAMPS provenance
      -> adaptor COMMITS through its own sink

**The Dreamer holds no write tool by default, and the one exception is
named.** A `write_fact` tool is an act verb pointed at the database instead of
at the group, and it would defeat the screen: if the model writes directly
there is no sink left to screen at. The model proposes; code disposes — the
same asymmetry every adaptor has, where the model's envelope is parsed into
typed proposals and a SINK decides what actually lands.

The exception (design/dreamer-agent-loop/39 D3, DEC-162; DEC-133 (1)
deprecated in part) is the proactive adaptor's `remind`: ONE act verb, for
ONE adaptor, admitted past the read-only ceiling by an `AdaptorGrant` the
composition root hands to the runtime BY NAME. It is not a write tool in the
sense above — the model's text is screened by `dream_screen` inside the
closure before `create`, and every other field of the row (kind, owner,
destination, payload) is stamped by CODE under a `dream_adaptor` provenance
the tool layer reads exactly as it reads `fire_turn`. The decision IS the
tool call; the envelope becomes bookkeeping (39 D4). The Dreamer still
cannot speak: `deliverer=None` is unchanged, and R-1b's two send paths are
untouched, because a row-driven occasion is a fifth occasion on the second
path, not a third path.

**Why the runtime cannot use `create_agent`, which no spec says.**
`create_agent` requires a `ReplyGuard` — a deliverer, a set of terminal verbs,
and the `tool_choice` relaxation — and `create_model_provider` hardcodes
`tool_choice="required"`, which that module documents as load-bearing. It IS
load-bearing, *for a turn that must end in `send_reply`*: under `required` the
model must call something on every pass, so a loop with no terminal verb can
never stop by itself, because the guard's `BeforeModelCallEvent` hook is the
only thing that ends a turn and it ends it by observing a terminal call
(DEC-21, DEC-32). The Dreamer has no terminal verb by construction — §4 of the
keystone removes every act verb and the terminal ones ARE act verbs — so it
needs its own guard (`DreamGuard`) and its own stopping condition: the model
declining to call anything, under `tool_choice="auto"`, inside a wall clock.

**Validation, and the line this module draws rather than defers.** `jsonschema`
is not a dependency and this does not add one — a new runtime dependency for one
call site is not proportionate. So: the RUNTIME validates the ENVELOPE (parses
as JSON, is an object, carries the adaptor's declared key, is a list under a
size cap, every element is a mapping) and the ADAPTOR's `accept` does the typed
parse, dropping a malformed item silently rather than raising — exactly what
`_parse_observations` / `_parse_item` already do. Recorded here because it is
the sort of decision that gets re-litigated by whoever adds the fourth adaptor.

**R-27 at this layer, and the mapping is the point.** A model response that
will not parse is `unavailable` («this batch was never actually read»), never
`nothing_found` («there was nothing in it») — because the loop must not
advance a watermark past a batch nobody read. The room-language consolidator
protected this by RAISING `ExtractionUnparseableError`; that consolidator went
with the language tier on 2026-09-07, and the outcome type is where the same
guarantee lands now that the adaptor contract returns a value instead.

**The instruction digest is read PER RUN, never cached at construction**
(DEC-128). The corpus hot-reloads off a bind mount; a digest captured at
`__init__` keeps naming the corpus as it was at process start — forever, and it
looks correct the whole time, because the wrongly-stamped rows are
indistinguishable from right ones. Read once at the top of a run so a reload
landing mid-run cannot split one batch's rows across two digests, and hash only
what was actually SENT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..domain.models import TranscriptRow
from ..domain.ports import DreamAdaptor, Proposal, RosterSource
from ..obs.metrics import Metrics
from . import factory
from .dream_guard import DreamGuard
from .dream_screen import screen
from .registry import ToolRegistry

log = logging.getLogger("nora.dreamer")

# The envelope's bounds. Both are REJECT-shaped rather than truncate-shaped for
# the text and DROP-shaped for the items, and the difference is deliberate: a
# response 20x the size of any legal one is evidence the model was doing
# something other than answering, so nothing in it is trustworthy; a response of
# the right shape carrying too many items is an ordinary over-eager batch, and
# keeping the first N of it is better than discarding a room's whole tick.
MAX_ENVELOPE_CHARS = 200_000
MAX_ITEMS = 64

# Tool-result caps (design/dreamer-agent-loop/32 §3.3 point 3).
# `MAX_ENVELOPE_CHARS` bounds the OUTGOING envelope only; before these, a
# tool result re-entered the loop UNTRUNCATED, so one fetched page or memory
# dump could spend the run's whole token budget on context. `DreamGuard`
# enforces the cap in its after-tool hook and appends a marker naming the
# cap, so a truncated result reads as truncated rather than as the whole
# answer. Memory verbs get the tighter number: their answers are summaries
# to reason over, not pages to quote.
MAX_TOOL_RESULT_CHARS = 8000
MAX_MEMORY_RESULT_CHARS = 4000
_TOOL_RESULT_CAPS: Mapping[str, int] = {
    "web_fetch": MAX_TOOL_RESULT_CHARS,
    "web_search": MAX_TOOL_RESULT_CHARS,
    "search_memory": MAX_MEMORY_RESULT_CHARS,
    # The workspace variant (32 §3.4) is the same verb at a wider scope —
    # same tighter number, same reason: summaries to reason over, not pages
    # to quote.
    "search_memory_workspace": MAX_MEMORY_RESULT_CHARS,
    "about": MAX_MEMORY_RESULT_CHARS,
    "ask_memory": MAX_MEMORY_RESULT_CHARS,
}

DreamStatus = Literal["ok", "nothing_found", "unavailable"]


@dataclass(frozen=True)
class AdaptorBudget:
    """One adaptor's ceiling. PER ADAPTOR, never one global number: proving a
    fact is worth more model calls than glossing a word, and a single ceiling
    would either starve the first or overpay for the second.

    **`timeout_seconds` is not optional, and the reason is written up at
    `nora/app/composition.py:145-164` from the last time it was got wrong.**
    Unlike a tag turn's timeout, a hung Dreamer call is QUIET: `dream_loop`
    marks the key `running` and skips it on every subsequent tick
    (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising, so
    the exponential backoff — which only engages on an exception — never fires
    and a hung provider is skipped forever rather than backed off. The wall
    clock wraps the WHOLE loop, the way `nora/engagement/stages/invoke.py` and
    `nora/app/fire.py` each wrap theirs.
    """

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class AdaptorSchedule:
    """One adaptor's own clock (design/dreamer-agent-loop/32 §3.1).

    A NAME-KEYED MAPPING value, like `AdaptorBudget`, and deliberately NOT a
    field on the `DreamAdaptor` protocol: a defaulted non-method member on a
    `@runtime_checkable` protocol fails `isinstance` for every adaptor that
    does not declare it (measured on Python 3.13), which would break all six
    existing adaptors at once. An adaptor with no entry in the mapping keeps
    its unscheduled behaviour — a watermark-bearing adaptor the shared
    `[language]` schedule (`lag > safety_lag`, `quiet_seconds`,
    `max_age_seconds`), a watermark-free one every tick — exactly as an
    adaptor with no budget entry gets `default_budget` by omission.

    `min_interval_seconds` is the floor between runs, read two ways. A
    watermark-BEARING adaptor fires when there is unread backlog (`lag > 0`)
    AND the watermark row's `updated_at` is at least this old. A
    watermark-FREE adaptor has no cursor to age, so it fires on its SLOT
    alone — `floor((now - phase_offset) / min_interval_seconds)`, no backlog
    required (design/dreamer-agent-loop/33 §5 N1a), which is what lets a
    time-driven cron fire in a silent room. `batch_limit` replaces the shared cap
    for that adaptor's `record.since` read. `max_items` overrides the
    runtime's envelope cap (`MAX_ITEMS`) for that adaptor — a daily
    1000-message batch can legitimately yield more than 64 candidate claims,
    and the runtime DROPS past the cap while the watermark still advances, so
    the excess would be lost with no error and no re-run. `None` keeps the
    shared default.

    `phase_offset_seconds` shifts a watermark-free adaptor's slot boundary,
    and it exists because separating PERIODS is not separating PHASES. The
    knowledge tier's three adaptors (deleted 2026-09-07, issue #115) had a
    real data dependency — candidates wrote a queue, adjudication drained it,
    entity resolution read what adjudication wrote — while `dream_loop`
    dispatches adaptors as CONCURRENT tasks with no ordering
    (design/dreamer-agent-loop/33 §6 M6 states this and declines to solve it
    for the note tier, which has no such dependency). The mechanism stays
    because the note tier's own slots use it (`note-curation` at 16:00 UTC).
    Give all three the same daily interval and they coincide every day; give
    them the same interval at different offsets and a claim written in the
    morning is adjudicated the same afternoon rather than tomorrow. Ignored
    for a watermark-BEARING adaptor, whose fire time is set by its watermark's
    `updated_at` and has no slot to shift.
    """

    min_interval_seconds: float
    batch_limit: int
    max_items: int | None = None
    phase_offset_seconds: float = 0.0
    #: Model calls per fire, UNIONED (design/dreamer-agent-loop/37, issue
    #: #118). Measured 2026-09-08 on `note-candidates`: one shape of entry —
    #: a plan one person states with an unsettled corner — is recognised in
    #: ~30% of samples at temperature 0.7 while precision stays 1.00 on every
    #: negative sample across every prompt variant tried. Three prompt-level
    #: changes did not move it. Sampling exploits the temperature already
    #: paid for: the union of N samples recognises it with probability
    #: 1 - 0.7^N (N=3 → 0.66, N=5 → 0.83) and, because per-sample invention
    #: is measured at ~0, stays empty on a negative batch. Duplicates across
    #: samples collapse in the adaptor's own staging key and create gate. A
    #: sample that is unreadable is counted and skipped; the run is
    #: `unavailable` only when EVERY sample was.
    #:
    #: **Samples run in SEQUENCE, so the run's worst case is N x the budget's
    #: timeout.** They are not gathered: a background pass nobody waits on
    #: buys nothing from parallelism except N concurrent calls at the
    #: gateway, and `dream_loop` already holds a per-`(adaptor, key)`
    #: `running` set, so a long run is skipped by the next tick rather than
    #: piling up behind itself.
    samples: int = 1

    def __post_init__(self) -> None:
        if self.samples < 1:
            raise ValueError(f"samples must be >= 1, got {self.samples}")


@dataclass(frozen=True)
class DreamOutcome:
    """What one run did, in R-27's three states.

    `unavailable` is never absent and never silently a zero: the Dreamer has
    nobody waiting on it, so a run that failed and a run that found nothing
    look identical from outside unless the difference is carried here.

    `stop_reason` is Strands' own, passed through. When one of the three caps
    binds, Strands raises nothing and logs nothing — `AgentResult.stop_reason`
    is the ONLY channel that says a loop was cut off rather than finished
    (R-23, verified end to end against 1.50.2). A runtime that dropped it would
    report a budget-exhausted loop exactly the way it reports a healthy quiet
    one.
    """

    status: DreamStatus
    written: int
    instruction_sha256: str
    stop_reason: str | None = None


@dataclass(frozen=True)
class HomeRoom:
    """The one room of a workspace a workspace-keyed adaptor may STAND IN
    (design/dreamer-agent-loop/39 D2, `policy.toml`'s `home_room = true`).

    Built by the composition root from `Policy.home_rooms()` and handed to
    `RegistryToolSource`; the tool source never reads policy itself. Every
    field is what `ToolRegistry.build` needs to build the room-scoped tools
    exactly as a tag would: `conversation_id` and `conversation_type` for
    the bound conversation, `platform` for the roster source and the
    reminder row's destination, `location_trusted` from the room's own
    `location` flag (DEC-74 — the flag `RegistryToolSource.build` left at
    its `False` default before this, so `where_is` refused in every
    configuration).
    """

    workspace: str
    platform: str
    conversation_id: str
    conversation_type: str = "group"
    location_trusted: bool = False


@dataclass(frozen=True)
class AdaptorGrant:
    """What ONE named adaptor holds PAST the Dreamer's defaults (39 D2/D3).

    A NAME-KEYED MAPPING value like `AdaptorBudget` and `AdaptorSchedule`,
    for the reason those are: a defaulted non-method member on the
    `@runtime_checkable` `DreamAdaptor` protocol fails `isinstance` for every
    adaptor that does not declare it. An adaptor with no entry gets
    `AdaptorGrant()` — no act verbs, no home room — which is the whole
    pre-DEC-162 ceiling, by omission.

    `acts` are act-verb NAMES admitted past `read_only_tools()` for this
    adaptor and no other. The three enforcement points all read it —
    `tool_subset_faults` (startup and per run) and `RegistryToolSource.build`
    — so an allowance is never a fourth list that can drift from the three.

    `home_room` says the adaptor's `Room` is built from the workspace's home
    room with a real roster source, `self_ids` and `location_trusted`, and
    that `WORKSPACE_SAFE_TOOLS` does not apply to it: the refusal that
    constant records («binding one to an arbitrary room of that workspace
    would be a disclosure decision nobody has made») is answered by the
    operator naming the room in `policy.toml`.
    """

    acts: frozenset[str] = frozenset()
    home_room: bool = False


def proactive_grant() -> AdaptorGrant:
    """The proactive adaptor's grant (39 D3): every read verb through the home
    room, and exactly one act verb — her own `remind`, which under the
    `dream_adaptor` provenance stamps kind/owner/destination/payload in code
    and screens the text before `create`. Not `send_reply` (the Dreamer does
    not speak), not `send_to`, not `note_create`/`note_update` (39 D6).

    A function returning a fresh value rather than a module-level constant,
    for DEC-13's reason (`tests/architecture/test_no_module_level_mutable_
    singletons.py`): the composition root builds one and hands the SAME
    object to the runtime and the startup check, which is the identity that
    matters — not one shared across every importer of this module.
    """
    return AdaptorGrant(acts=frozenset({"remind"}), home_room=True)


class DreamToolSource(Protocol):
    """The tools an adaptor may hold, resolved by NAME.

    A documented structural type rather than a `nora.domain.ports` Protocol,
    the same reason `nora.app.runtime._InstructionVersions` and the observers'
    `_ObjectiveSource` are: a shape with one consumer does not earn a shared
    contract suite.

    It exists because building a tool is not free of context — `load_transcript`
    needs a store and a conversation, `about` needs Honcho, `web_search` needs
    the provider — and the composition root is the only place that holds all of
    them. The runtime asks for names and gets objects; it never learns what is
    behind them, which is the same relationship the Dreamer already has to
    `SearchProvider`.

    **`read_only()` is the CEILING and `build` must enforce it.** An adaptor
    declares tool names because `nora.language` may not import `nora.agent`,
    which means nothing about the declaration itself stops it naming
    `send_reply`. `verify_tool_subsets` below is the startup refusal;
    `DreamerRuntime.run` re-checks per run so a subset that changed under a
    hot-reload cannot slip through as a quiet grant.

    `grant` is the ONE allowance past that ceiling (39 D3), threaded from the
    runtime's name-keyed mapping so `build` and the two checks read the same
    value; `adaptor` is the run's provenance, stamped onto the tools as
    `dream_adaptor` the way the fire path stamps `fire_turn`.
    """

    def read_only(self) -> frozenset[str]: ...

    def build(
        self, *, key: str, names: frozenset[str], workspace: str | None = None,
        adaptor: str = "", grant: AdaptorGrant | None = None,
    ) -> list[Any]: ...


# The read-only tools a WORKSPACE-keyed adaptor may hold, and the reason the
# set is exactly this.
#
# Every ROOM-scoped read tool stays refused here — `load_transcript` reads one
# room's record, `group_members`/`lookup_person`/`about`/`ask_memory`/
# `search_memory` all answer against one room's live roster, and
# `recall_lookups`/`media_fetch` replay one room's own calls. A workspace has no
# single room to bind them to, and binding one to an arbitrary room of that
# workspace would be a disclosure decision (R-31) nobody has made.
#
# The WORKSPACE-scoped read tools are here, and they are here because the key
# path is now SUPPLIED (design/dreamer-agent-loop/32 §3.4): a workspace-keyed
# adaptor's key IS the workspace, and `DreamerRuntime.run` hands it through
# `RegistryToolSource.build(workspace=...)` to `build_tool_specs`, where
# `_workspace()` returns it directly instead of deriving it from the room.
# Before that fix this set deliberately EXCLUDED the note verbs: they resolve
# their workspace through the ROOM — `workspace_for(
# room.platform, conversation_id)` — and `RegistryToolSource.build` hands a
# workspace-keyed adaptor a `Room` with no platform and the workspace in the
# conversation slot, so policy could never name it and every call answered
# `unavailable` forever. That is `08-state.md` §10's stated trap: a tier read
# tool for a workspace-keyed adaptor needs a key path that does not route
# through the room at all, because the Dreamer's key already IS the workspace
# and must be SUPPLIED rather than derived — "the difference between the seam
# working and the seam looking like it works while every call fails closed".
#
# The trap still binds for SCOPE-keyed adaptors: they get `workspace=None`,
# `_workspace()` falls back to the resolver, and the resolver reads the real
# room as before. Nothing about the tag path changes — the supplied value
# only ever WINS when a caller supplies one, and only the Dreamer does.
#
# An adaptor whose `AdaptorGrant.home_room` is set is EXEMPT from this set
# (39 D2/D3): the home room is the disclosure decision the first paragraph
# says nobody has made, and it is made per workspace in `policy.toml`. Every
# other workspace-keyed adaptor stays bound to it.
#
# Hand-listed HERE and pinned by a test that measures it against
# `read_only_tools()`, so a read tool added tomorrow is UNCLASSIFIED and turns
# that test red rather than silently joining or silently missing. That is the
# same structure `ACT_VERBS` uses in `tests/unit/test_tool_registry.py`, and it
# is what keeps a hand-list from going stale the way the pre-DEC-29 catalogue
# did.
WORKSPACE_SAFE_TOOLS: frozenset[str] = frozenset({
    "web_search", "web_fetch", "now", "calculate", "weather", "version",
    # `social_fetch` (DEC-151): reads the open internet (public X/Instagram
    # content) with no room state anywhere in the answer — the same shape as
    # `web_search`/`web_fetch`, and read-only at three layers besides (R-86).
    "social_fetch",
    # Nora's OWN generated-image library — reads her library across every
    # conversation (the store records platform/conversation as provenance,
    # but a lookup is a global search of her own creations, never
    # conversation-scoped like `load_transcript`).
    "media_search",
    # The workspace-scoped reads, admitted with the supplied key path
    # (32 §3.4): the note tier's two read verbs and the workspace-wide memory
    # variant of `search_memory`. Each resolves its workspace from the
    # SUPPLIED key rather than from a room a workspace-keyed adaptor does
    # not have.
    "note_read", "note_search",
    "search_memory_workspace",
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
    """An adaptor asked for a tool outside the read-only ceiling, or for one
    that does not exist. Raised at STARTUP by `verify_tool_subsets` — R-5's
    refuse-to-start shape, because a background agent holding an act verb is
    not a condition to discover at 3am from a metric."""


def verify_tool_subsets(
    adaptors: Sequence[DreamAdaptor], *, tools: DreamToolSource,
    grants: Mapping[str, AdaptorGrant] | None = None,
) -> None:
    """Refuse to start if any adaptor's declared subset escapes the ceiling.

    The hard line — the Dreamer may look and may not act, save the one named
    allowance in `grants` — is enforced in two places on purpose, and neither
    is redundant. Here it is a startup refusal, which is where a wiring
    mistake belongs (`policy.toml`'s own precedent: she refuses to start
    rather than run an ambiguous policy). In `run` it is a per-run re-check,
    because the objective corpus hot-reloads and an adaptor that is rebuilt
    or swapped between ticks would otherwise be measured once, at a moment
    that has passed.

    `grants` is the SAME mapping the runtime holds (the composition root
    builds one and passes it to both), so the startup check measures exactly
    what the runtime enforces.
    """
    ceiling = tools.read_only()
    for adaptor in adaptors:
        grant = (grants or {}).get(adaptor.name, AdaptorGrant())
        for message in tool_subset_faults(adaptor, ceiling=ceiling, grant=grant):
            raise AdaptorToolError(message)


def tool_subset_faults(
    adaptor: DreamAdaptor, *, ceiling: frozenset[str],
    grant: AdaptorGrant | None = None,
) -> tuple[str, ...]:
    """Every way this adaptor's declared subset is wrong, as messages.

    Two axes, and they are independent — a tool can be read-only and still be
    unusable by this adaptor:

    1. **read vs act.** Outside `read_only_tools()` is an act verb, and no
       background loop may hold one — except the verbs `grant.acts` names
       for THIS adaptor (39 D3: the proactive adaptor's `remind`, and
       nothing else today).
    2. **room-scoped vs workspace-safe.** A WORKSPACE-keyed adaptor gets a
       workspace name as its `key`, which is not a conversation id. Every
       room-scoped read tool is bound to one — `load_transcript` reads one
       room's record, `about`/`ask_memory`/`search_memory`/`group_members`
       answer against one room's live roster — so handing it a workspace does
       not fail loudly, it returns EMPTY. `load_transcript` on an unknown
       conversation returns `{"messages": []}`, which is `unavailable`
       reported as `nothing_found`: the one substitution R-27 forbids.
       An adaptor whose grant names a HOME ROOM (39 D2) is exempt: it does
       have a room to bind them to, and the operator chose it.

    Split out of `verify_tool_subsets` so `DreamerRuntime.run` re-checks the
    identical rule rather than a second copy of it — the startup check and the
    per-run check disagreeing is worse than either being absent, because the
    startup one is the one people trust.

    **The second axis was documented and unenforced until 2026-08-21.** This
    function's docstring, `RegistryToolSource`'s, DEC-133 and the release
    fragment all asserted the restriction; nothing applied it, and a review
    probe built `about`/`load_transcript`/`search_memory` for a workspace key
    with no complaint. A protection three documents claim exists is worse than
    one nobody claims, because the next reader trusts it.
    """
    allowance = grant if grant is not None else AdaptorGrant()
    faults: list[str] = []
    declared = frozenset(adaptor.tools)
    escaped = declared - ceiling - allowance.acts
    if escaped:
        faults.append(
            f"Dreamer adaptor {adaptor.name!r} declares tools outside the "
            f"read-only ceiling: {', '.join(sorted(escaped))}. The Dreamer "
            f"may look and may not act; a tool is grantable only if its "
            f"NativeTool row says read_only=True, or an AdaptorGrant names "
            f"it for this adaptor (39 D3)."
        )
    if adaptor.key_kind == "workspace" and not allowance.home_room:
        room_scoped = declared - WORKSPACE_SAFE_TOOLS
        if room_scoped:
            faults.append(
                f"Dreamer adaptor {adaptor.name!r} is keyed on a WORKSPACE and "
                f"declares room-scoped tools: {', '.join(sorted(room_scoped))}. "
                f"A workspace is not a conversation, so these would answer "
                f"EMPTY rather than fail — «I could not check» reported as "
                f"«there was nothing». Workspace-safe tools are: "
                f"{', '.join(sorted(WORKSPACE_SAFE_TOOLS))}."
            )
    return tuple(faults)


def _envelopes(raw: str, *, key: str):
    """Every VALID envelope in `raw`, in order — an object carrying `key`
    whose value is a list of mappings.

    **`raw_decode` at each `{`, not hand-rolled brace counting.** The first
    version counted braces with its own quote and escape tracking, and a
    review broke it three ways: an unterminated quote in prose swallowed the
    rest of the reply (`'no source for the 5" pipe'` is not exotic for a
    research pass), an unclosed `{` did the same, and the escape handling the
    docstring credited was pinned by no test at all — no test in the file
    contained a `\\"`. `json.JSONDecoder.raw_decode` is the real parser; it
    gets strings, escapes and nesting right by construction, and a candidate
    that fails simply advances to the next `{`.

    **Validity is checked HERE rather than by the caller**, because a
    malformed object carrying the key would otherwise SHADOW a well-formed one
    after it: `{"verdicts": ["a string"]}` followed by a real envelope has to
    yield the real envelope, not `None`.
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
    """The model's raw text -> the declared key's items, or `None` for any
    shape that is not exactly `{"<key>": [ {...}, ... ]}`.

    `None` means the batch was NOT READ — an empty body, truncated JSON and a
    wrong top-level key all land here, and the caller maps every one of them
    to `unavailable`. It is deliberately NOT the same value as an empty list,
    which is a well-formed «I looked and found nothing».

    **A PREAMBLE is no longer one of them.** Two documents forbid exactly this
    by name and both are overturned here rather than ignored:
    `docs/superpowers/specs/2026-08-21-dreamer-live-transfer.md` §91 — "do not
    fix this by loosening `parse_envelope`… fix it at the objective" — and
    `07-loop.md` §57, which wants the envelope to be a terminal tool call
    rather than text parsed out of a reply.

    The first is overturned by evidence it did not have: the objective fix
    LANDED. The (since deleted) `fact-adjudication.md` spent its closing
    paragraph on «your entire reply is one JSON object, the first character
    you write is `{`», and the model narrated anyway — measured below, and
    the surviving objectives carry the same closing paragraph. An instruction that has
    been given and is not followed is not a fix that is still pending.

    The second is not overturned, it is deferred: a terminal tool call is the
    better shape and it is a change to every adaptor and to the guard that
    ends a Dreamer turn. This is the reading that would reopen it — if
    narration keeps costing batches after this, the parser is the wrong layer
    and 07 §57 is where to go.

    Measured 2026-08-28, driving the then-shipped `fact-adjudication`
    objective through its whole tool loop (the objective is gone; the
    measurement is why the parser tolerates narration):

      effort=none, empty results  converged turn 6 — «Search returning empty
                                  results consistently. Cannot find any
                                  source... \n\n{"verdicts": [...]}»
      effort=none, rich results   converged turn 6, same shape
      effort=low, either          never converged in 8 turns

    So the model DOES produce the envelope; it narrates first. `json.loads`
    over the whole string then fails, the run reports `unavailable`, and the
    watermark is held — so a batch that WAS read is re-read forever while the
    counter says the model could not be reached. Discarding a readable answer
    because a sentence preceded it turns a `nothing_found` into an outage,
    which is R-27 upside down.

    The strictness that matters is kept: the envelope must still be a JSON
    object carrying the declared key, whose value is a list of mappings. What
    is dropped is only the requirement that it be the FIRST thing in the
    reply. The objective still tells the model to write nothing else — a
    prompt asking for less than the parser accepts is the safe direction, and
    it is the direction every one of these objectives already takes.

    Never raises. `_parse_observations`' own posture, and for the same reason:
    the alternative is one malformed response taking down a tick that four
    other adaptors were sharing.
    """
    if len(raw) > MAX_ENVELOPE_CHARS:
        log.warning(
            "dream envelope rejected: %d chars exceeds the %d cap",
            len(raw), MAX_ENVELOPE_CHARS,
        )
        return None
    # THE LAST valid envelope, not the first. Every objective prints the empty
    # form — `{"verdicts": []}` — as its nothing-found instruction, so a model
    # that quotes its instructions on the way to answering puts a VALID but
    # empty envelope ahead of its real one. Taking the first yields zero
    # items, which is `nothing_found`, which ADVANCES THE WATERMARK: real
    # verdicts discarded and the batch consumed permanently, with every
    # counter green. That is silent and irreversible where the bug it replaced
    # was loud and retryable.
    #
    # Worse than lossy on an objective that prints a POPULATED example (the
    # deleted `fact-adjudication` did; `note-candidates` does): the tier would
    # write the documentation's own worked example as a real row.
    #
    # Last is also what the measurement says: the model narrates, then
    # answers. Nothing pinned first-vs-last before this comment existed —
    # mutating one to the other left the suite green, which is how a policy
    # nobody decided ends up load-bearing.
    envelopes = list(_envelopes(raw, key=key))
    if not envelopes:
        return None
    return tuple(envelopes[-1][key])


class RegistryToolSource:
    """`DreamToolSource` over the real `ToolRegistry` — the only place that
    turns an adaptor's tool NAMES into tool objects.

    Two guards, and they are independent on purpose:

    1. **The read-only ceiling.** Only a name in `read_only_tools()` is ever
       built, so an act verb cannot reach a background loop even if an adaptor
       asks for one and even if `verify_tool_subsets` was skipped.
    2. **An empty room.** The `Room` handed to `ToolRegistry.build` has NO
       roster source, so its view is `available=False` — «I could not look» —
       and any room-scoped tool that did slip through discloses nothing rather
       than answering from a room the Dreamer was never given. That is a
       DATA-level fail-closed guard behind the name-level one, because the
       name-level one is a list and lists are the thing that go stale.

    A workspace-keyed adaptor is additionally restricted to
    `WORKSPACE_SAFE_TOOLS` (see that constant), because a workspace has no room
    at all — `key` is not a conversation id there, and passing it as one would
    quietly ask a room-scoped tool about a conversation that does not exist.

    **Both guards have one named exception, and it is the same one** (39
    D2/D3). An adaptor whose `AdaptorGrant` names a home room is built FROM
    that room — the real roster source for its platform, Nora's own
    `self_ids` so her account is skipped rather than counted `unnamed`, and
    `location_trusted` from the room's `location` flag — so `where_is` and
    its siblings run the disclosure guard exactly as on a tag; and its
    `grant.acts` are admitted past `read_only()`. Everything about the
    build that is not the room or the acts is unchanged: `deliverer=None`,
    and the workspace is still SUPPLIED. A home-room grant for a workspace
    that has no home room falls back to the closed shape above and says so
    — never a guessed room.
    """

    def __init__(
        self, *, registry: ToolRegistry, store: Any, persons: Any, honcho: Any,
        metrics: Metrics, ai_peer: str = "nora",
        home_rooms: Mapping[str, HomeRoom] | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._store = store
        self._persons = persons
        self._honcho = honcho
        self._metrics = metrics
        self._ai_peer = ai_peer
        # Workspace -> home room, from `Policy.home_rooms()` (39 D2). Empty
        # for every composition that names none, which builds exactly what
        # it built before.
        self._home_rooms = dict(home_rooms or {})
        # Platform -> roster source, the same map `FirePath` holds: a home
        # room on a platform with no roster answers `available=False`, the
        # honest state, never an empty room.
        self._rosters = dict(rosters or {})
        self._self_ids = frozenset(self_ids)

    def read_only(self) -> frozenset[str]:
        return self._registry.read_only_tools()

    def build(
        self, *, key: str, names: frozenset[str], workspace: str | None = None,
        adaptor: str = "", grant: AdaptorGrant | None = None,
    ) -> list[Any]:
        from .room import Room

        allowance = grant if grant is not None else AdaptorGrant()
        home = self._home_rooms.get(workspace or "") if allowance.home_room else None
        if allowance.home_room and home is None:
            # Loud, and CLOSED: the grant said «stand in the home room» and
            # the workspace has none. Building the room-scoped tools against
            # a synthetic room would answer «nobody is here» (`available=
            # False`) and `remind` would write into a conversation that does
            # not exist, so neither is built — the acts are withheld along
            # with the room.
            log.error(
                "dream adaptor %r is granted a home room but workspace %r "
                "names none in policy.toml; building without acts or a room",
                adaptor, workspace,
            )
        acts = allowance.acts if home is not None else frozenset()
        wanted = frozenset(names) & (self.read_only() | acts)
        if not wanted:
            return []
        if home is not None:
            room = Room(
                platform=home.platform, conversation_id=home.conversation_id,
                roster_source=self._rosters.get(home.platform),
                persons=self._persons,
                # No tagger: the run is owed to nobody. `Room.scope()`'s
                # degraded branch therefore narrows to nothing when the
                # roster cannot be read, and `where_is` answers
                # `unavailable` there — honest, not a hole (39 §0).
                self_ids=self._self_ids,
            )
        else:
            room = Room(
                platform="", conversation_id=key, roster_source=None,
                persons=self._persons,
            )
        built = self._registry.build(
            store=self._store,
            # The HOME ROOM's conversation, not the key, when there is one
            # (39 D3): for a workspace-keyed adaptor the key is the
            # workspace NAME, and a reminder row bound to it would die
            # `failed_policy` at every fire.
            conversation_id=home.conversation_id if home is not None else key,
            conversation_type=home.conversation_type if home is not None else "group",
            persons=self._persons,
            honcho=self._honcho,
            # No deliverer, ever. Nothing the Dreamer holds speaks to anybody,
            # and the filter below cannot admit a tool that would — but the
            # absence is stated here too, because a `None` that was once a
            # deliverer is how one gets passed back in.
            deliverer=None,
            metrics=self._metrics,
            room=room,
            # DEC-74's flag, threaded from the home room exactly as a tag
            # threads it from `Conversation.location`. `False` — the strict
            # gate — for every build with no home room, as before.
            location_trusted=home.location_trusted if home is not None else False,
            # This run's PROVENANCE, read by `remind` the way it reads
            # `fire_turn`: under it the closure stamps the row in code
            # (39 D3) instead of reading a tagger it does not have.
            dream_adaptor=adaptor,
            # The key-path fix (design/dreamer-agent-loop/32 §3.4): for a
            # WORKSPACE-keyed adaptor `key` IS the workspace, so the runtime
            # SUPPLIES it and the workspace-scoped verbs (the note reads,
            # `search_memory_workspace`) read it directly
            # rather than deriving one from the synthetic room above — where
            # `workspace_for("", key)` can only answer `None`. `None` for a
            # scope-keyed adaptor, whose key is a real conversation id and
            # whose tools resolve through the room exactly as the tag path's
            # do.
            supplied_workspace=workspace,
        )
        return [
            tool for tool in built
            if (getattr(tool, "tool_name", None) or getattr(tool, "__name__", ""))
            in wanted
        ]


class DreamerRuntime:
    """One public entry point: `run`.

    Collaborators through `__init__` only, per the test-seam policy — no
    `*_fn` hooks, no `_for_test` parameters. `budgets` is a mapping from
    adaptor name to its ceiling; an adaptor with no entry gets `default_budget`
    rather than a `KeyError` or an unbounded loop, because an adaptor added
    later must be bounded by omission rather than unbounded by it.

    `schedules` is the same shape for the run's other per-adaptor number: an
    adaptor whose `AdaptorSchedule.max_items` is set gets that envelope cap
    instead of `MAX_ITEMS` (design/dreamer-agent-loop/32 §3.1 — the cap is
    DROP-shaped while the watermark still advances, so a daily 1000-message
    extraction batch would lose every claim past 64 with no error and no
    re-run). An adaptor with no entry keeps the shared cap.
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
    ) -> None:
        self._models = models
        self._instructions = instructions
        self._tools = tools
        self._budgets = dict(budgets)
        self._default_budget = default_budget
        self._retry = retry
        self._metrics = metrics
        self._model_name = model_name
        # `| None` rather than `= {}`: ruff's B006 is right that a mutable
        # default shared across instances is a trap, and this mapping is only
        # ever READ, so coalescing here costs nothing.
        self._schedules = dict(schedules or {})
        # `[llm.budgets]`, the per-deployment delta over `NATIVE_TOOLS`.
        #
        # **The Dreamer did not see it until 2026-09-07, and the tag path
        # did.** `ToolRegistry(budget_overrides=cfg.llm.budgets).budgets()`
        # applies it for a turn; `_tool_budgets` below read `spec.budget`
        # straight off the table, so one number in `config.toml` meant two
        # different ceilings depending on which loop was asking — with nothing
        # naming the difference. Measured live on 2026-09-07, the first tick
        # after the (since deleted) knowledge tier's schedule landed:
        # `[llm.budgets] web_fetch = 6` was
        # set, and `DreamGuard` vetoed the Dreamer at «you already called
        # web_fetch 2 time(s)», the TABLE value, twelve times in one run that
        # then produced a 0-char envelope.
        #
        # A plain `Mapping[str, int]` through `__init__` rather than a config
        # read: `nora.agent` may not import `nora.config` (the layer contract),
        # which is the same reason `max_adjudications_per_tick` arrives as an
        # int on the adaptors.
        self._tool_budget_overrides = dict(tool_budget_overrides or {})
        # Name -> `AdaptorGrant` (39 D2/D3): the ONE allowance past the
        # read-only ceiling and past `WORKSPACE_SAFE_TOOLS`, per adaptor, by
        # name. The same mapping the composition root hands
        # `verify_tool_subsets`, so the startup check and the per-run
        # re-check below cannot disagree about who holds what. Empty by
        # omission, which is the whole pre-DEC-162 ceiling for every adaptor.
        self._grants = dict(grants or {})

    def budget_for(self, adaptor_name: str) -> AdaptorBudget:
        return self._budgets.get(adaptor_name, self._default_budget)

    def grant_for(self, adaptor_name: str) -> AdaptorGrant:
        """This adaptor's allowance — `AdaptorGrant()` (nothing) for an
        adaptor with no entry, never a `KeyError` inside a background tick."""
        return self._grants.get(adaptor_name, AdaptorGrant())

    def samples_for(self, adaptor_name: str) -> int:
        """Model calls per fire — `AdaptorSchedule.samples`, 1 for an adaptor
        with no schedule entry."""
        sched = self._schedules.get(adaptor_name)
        return sched.samples if sched is not None else 1

    def max_items_for(self, adaptor_name: str) -> int:
        """This adaptor's envelope item cap — its schedule's `max_items`
        where set, else the runtime-wide `MAX_ITEMS`."""
        sched = self._schedules.get(adaptor_name)
        if sched is not None and sched.max_items is not None:
            return sched.max_items
        return MAX_ITEMS

    async def run(
        self, *, adaptor: DreamAdaptor, key: str, batch: Sequence[TranscriptRow]
    ) -> DreamOutcome:
        """One adaptor, one key, one bounded read-only loop.

        Every failure path returns `unavailable` and is COUNTED. Nothing here
        raises into the caller: `dream_loop`'s per-key try/except drives an
        exponential backoff against an external model provider, and an
        ordinary «the model returned prose» must not look like an outage to it.
        The exception is the tool-ceiling re-check, which is a wiring fault
        rather than a runtime one and is loud.
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
                # Loud, and `unavailable` rather than a raise: a background loop
                # must not take its own clock down, but this is a wiring fault
                # and reads as one. The IDENTICAL rule the startup check runs —
                # one function, two call sites — because a per-run check that
                # had drifted from the startup one would be worse than none:
                # the startup check is the one people trust.
                for fault in faults:
                    log.error("dream run refused: %s", fault)
                return self._finish(
                    adaptor, "unavailable", 0, sha, None, started,
                )

            tools = self._tools.build(
                key=key, names=frozenset(adaptor.tools),
                # A workspace-keyed adaptor's key IS the workspace (32 §3.4):
                # supply it so the workspace-scoped verbs resolve it directly
                # instead of asking `workspace_for` to name a room that does
                # not exist. A scope-keyed adaptor supplies nothing and its
                # tools resolve through the room, unchanged.
                workspace=key if adaptor.key_kind == "workspace" else None,
                adaptor=adaptor.name, grant=grant,
            )
            # The BUDGETS, not the guard. A `DreamGuard` owns a
            # `CallLedger`, and a ledger is SPENT STATE — build one per run
            # and the second sample starts with the first's tool calls
            # already counted against it, so an adaptor with N samples would
            # silently get a fraction of its declared budget per sample.
            # Inert for the two adaptors that ship (both tool-free) and
            # exactly the shape that stays inert until it does not, so the
            # guard is built inside `_sample` instead.
            tool_budgets = _tool_budgets(
                tools, adaptor, self._tool_budget_overrides)
            prompt = await adaptor.inputs(key=key, batch=batch)
            if not prompt.strip():
                # **THE FIRE CONDITION, and it is general rather than the fact
                # tier's.** A blank `inputs` means the adaptor has nothing to
                # reason about, and calling a model with nothing to reason
                # about is a research loop spent on an empty prompt.
                #
                # It matters most for an adaptor with `uses_watermark = False`,
                # which gets no lag/quiet/age conditions at all and would
                # otherwise fire on EVERY tick: at the shipped 300s cadence
                # that is 288 full `web_search`/`web_fetch` loops per workspace
                # per day with nothing pending. The port's own docstring says
                # such an adaptor "must carry its own fire condition", and this
                # is where the runtime lets it: the shipped curator's is a
                # daily slot plus a blank `inputs` on a tidy notebook.
                #
                # `commit(key, ())` is still called, so the decay contract is
                # untouched: a watermarked adaptor over a quiet room renders a
                # blank prompt too, and that path is exactly where a tier's
                # time-driven work lives — the curator records its wakes on an
                # empty commit, whatever the model did or did not say. Reported
                # `nothing_found` — «I looked and there was nothing» — because
                # that is what happened; `unavailable` would hold a watermark
                # over a batch that was genuinely empty.
                written = await adaptor.commit(
                    key=key, proposals=(), now_ms=int(time.time() * 1000),
                )
                return self._finish(
                    adaptor, "nothing_found", written, sha, None, started,
                )
        except Exception:
            log.exception(
                "dream run failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            return self._finish(adaptor, "unavailable", 0, sha, None, started)

        # THE MODEL CALL(S) — `samples` of them, unioned (`AdaptorSchedule.
        # samples`). Each sample is its own agent, its own timeout and its own
        # envelope parse; one unreadable sample is counted and skipped, and
        # the run is `unavailable` only when none was readable, because
        # «nobody read this batch» is what holds the watermark and one good
        # read is a read. `stop_reason` reported is the last readable
        # sample's.
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
        # THE CAP IS PER RUN, and with sampling that is per UNION rather than
        # per model answer. Identical items collapse first (above), so N
        # samples that agree cost one slot; N samples that each invent
        # something different are exactly the case a cap is for.
        max_items = self.max_items_for(adaptor.name)
        if len(items) > max_items:
            # Capped, never silently: a cap nobody counts reads as «we wrote
            # everything it found».
            dropped = len(items) - max_items
            log.warning(
                "dream run returned %d items, keeping %d (adaptor=%s key=%s)",
                len(items), max_items, adaptor.name, key,
            )
            # ONE increment carrying the count, not one call per item: the
            # envelope cap is 200k characters, so a pathological response can
            # hold tens of thousands of items and a per-item loop would spend
            # the tick inside prometheus rather than on the drop it is
            # reporting (2026-08-21 review).
            self._metrics.inc(
                "nora_dream_screened_total", float(dropped),
                adaptor=adaptor.name, reason="cap",
            )
            items = items[:max_items]

        kept, _rejected = screen(
            adaptor=adaptor.name, items=items, metrics=self._metrics,
        )

        try:
            payloads = await adaptor.accept(key=key, items=kept)
            now_ms = int(time.time() * 1000)
            proposals = tuple(
                Proposal(
                    payload=payload,
                    instruction_sha256=sha,
                    model=self._model_name,
                    # The run that produced it — the handle a later `supersede`
                    # names when it retires this row. Derived from the tick
                    # rather than minted at random so a row can be tied back to
                    # a trace without a lookup table.
                    established_by=f"dream:{adaptor.name}:{key}:{now_ms}",
                )
                for payload in payloads
            )
            # Called even when `proposals` is empty, and that is not a
            # formality: an empty `commit` is R-27's `nothing_found` and the
            # sink may act on it — the curator records its wakes on one,
            # because decay is time-driven. Skipping the call would drop decay
            # silently.
            written = await adaptor.commit(
                key=key, proposals=proposals, now_ms=now_ms,
            )
        except Exception:
            log.exception(
                "dream commit failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            return self._finish(
                adaptor, "unavailable", 0, sha, stop_reason, started,
            )

        # `ok` vs `nothing_found` is about what the MODEL returned, not about
        # how many rows survived: a batch whose every item was screened out, or
        # whose every verdict was `not_covered`, was still a batch that was
        # read. Collapsing them would make a working screen look like a silent
        # model.
        status: DreamStatus = "ok" if items else "nothing_found"
        return self._finish(adaptor, status, written, sha, stop_reason, started)

    async def _sample(
        self, *, adaptor: DreamAdaptor, key: str, instructions_text: str,
        sha: str, tools: list[Any], tool_budgets: Mapping[str, int],
        prompt: str, budget: AdaptorBudget,
    ) -> tuple[tuple[Mapping[str, object], ...] | None, str | None]:
        """ONE model call: build a guard, build the agent, invoke it under the
        budget, read the envelope. Returns `(items, stop_reason)`; `items is
        None` means this sample was not readable (timeout, exception, or no
        envelope) — counted by the caller, never raised.

        **The guard is built HERE, once per sample**, because it owns a
        `CallLedger` and a ledger is spent state: a shared one would let the
        first sample's tool calls count against the second's budget. The
        TOKEN budget (`budget`) is deliberately per sample too — it is the
        model's own limit for one call, and N samples of a bounded call is
        what sampling means.
        """
        guard = DreamGuard(
            adaptor=adaptor.name,
            budgets=dict(tool_budgets),
            metrics=self._metrics,
            result_caps=_TOOL_RESULT_CAPS,
        )
        try:
            agent = factory.create_dream_agent(
                model=self._models.create(),
                system_prompt=instructions_text,
                tools=tools,
                guard=guard,
                trace_attributes={
                    "dreamer.adaptor": adaptor.name,
                    "dreamer.key": key,
                    "dreamer.objective": adaptor.objective,
                    "dreamer.instruction_sha256": sha,
                },
                retry=self._retry,
                agent_name=f"dreamer:{adaptor.name}",
            )
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
            return None, "timeout"
        except Exception:
            log.exception(
                "dream run failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            return None, None
        stop_reason = getattr(result, "stop_reason", None)
        if stop_reason is not None:
            self._metrics.inc(
                "nora_dream_stop_reason_total",
                adaptor=adaptor.name, reason=str(stop_reason),
            )
        raw = factory.final_text(result)
        items = parse_envelope(raw, key=adaptor.envelope_key)
        if items is None:
            log.warning(
                "dream run produced no readable envelope (adaptor=%s key=%s, "
                "%d chars)", adaptor.name, key, len(raw),
            )
            return None, stop_reason
        return items, stop_reason

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
    """The union of N samples with byte-identical items collapsed, first
    occurrence kept in order. Two samples that phrase one arrangement
    differently are NOT collapsed here — that is the adaptor's job (the
    staging key and the create gate decide what is the same occasion), and a
    runtime that guessed would be deciding a tier's identity rule for it."""
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


def _tool_budgets(
    tools: list[Any],
    adaptor: DreamAdaptor,
    overrides: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """The per-tool call ceiling `DreamGuard` enforces under R-7.

    Read off the BUILT tools rather than off the adaptor's declared names, so a
    name the tool source declined to build is absent from `budgets` and is
    therefore vetoed by default-deny rather than admitted with a ceiling it
    cannot spend. `adaptor` is named only in the log line, which is what makes
    a mismatch findable rather than merely safe.

    `overrides` is `[llm.budgets]`, applied OVER the table exactly as
    `ToolRegistry.budgets()` applies it for a tag's turn — see
    `DreamerRuntime.__init__` for why the Dreamer went without it. Applied only
    to a tool that BUILT: an override naming a tool this adaptor does not hold
    must not conjure a budget entry, because a name in `budgets` is what turns
    default-deny into permission.
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
        budgets[name] = (overrides or {}).get(name, spec.budget)
    return budgets
