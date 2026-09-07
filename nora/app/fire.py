"""`FirePath` — one OCCASION to speak unprompted becomes one actual message.

WHAT THIS MODULE IS RESPONSIBLE FOR
-----------------------------------
The TURN. `nora.app.heartbeat` owns *when* a reminder fires, *whether* policy
still allows it and *what the row then says*; this owns everything between the
two — the synthetic `TagContext`, the prompt, the model call, the delivery, and
the §8.1 outcome string it answers with.

THERE ARE SEVERAL OCCASIONS, AND THE TURN IS THE SAME TURN
----------------------------------------------------------
A reminder coming due was the first (DEC-70). A version announcement was the
second (DEC-79): on the boot after an upgrade, the operator's release note is
put to her and she relays it in her own voice. Gatherings (DEC-139), compute
results (Nora Compute spec §2) and relays (DEC-105) followed; participation
(design/dreamer-agent-loop/35) is the latest — the FIRST occasion that triggers
on a real room going quiet rather than on a stored row, and the first where
silence is a HEALTHY outcome rather than a failure (`SILENT` below).
Everything between "something made this turn happen" and "the group heard it"
is identical — the doctrine
audience, the guards, the audit-first write, the Honcho feed, the Langfuse
trace — and R-1b (`tests/architecture/test_only_two_paths_can_send.py`) makes
that structural rather than optional: `deliver.build` has exactly TWO callers,
the tag pipeline and this module, so a second unprompted path cannot be built
beside this one. It has to be built THROUGH it.

So what differs is factored into `Event`: where she speaks, on whose
authority, the words put to her, and the header that says why this turn is
happening. `speak(occasion)` is the turn; `run(rows)` is the heartbeat's
reminder-shaped adapter onto it (see both docstrings).

It reuses the tag pipeline's stages where they fit and refuses them where they
do not, and the refusals are the design (design spec §5's table):

* **no `admission.run`** — R-9's debounce would reject a fire turn within five
  seconds of a tag AND consume the tag's window; R-30 sheds the NEWEST arrival,
  which is always the reminder that has waited hours; R-12 tests a synthetic
  sender against Nora's own aliases. The conversation slot is taken by the tick
  instead, through the same `ConversationSlots` the tag path holds.
* **no `audit_stage` and no `memory_feed_stage`** — both would write a message
  the requester never sent, under the requester's peer, at the wrong time.
* **no `indicate`** — `indicate.begin` reacts to `(author, timestamp)` and
  returns `None` when either is falsy, which the synthetic message's blank
  `sender_id`/`timestamp` make true BY CONSTRUCTION. Fabricating them means a
  failed `sendReaction` every tick, or 👀 then ⚠️ on an unrelated old message.
* **no `ContextAssembler.assemble`** — see `build_context`. This is the trap.
* **no `invoke.run` and no `deliver.check_outcome`** — see `run`.

WHAT IT DOES REUSE, AND WHY EACH ONE MATTERS
--------------------------------------------
`MentionRenderer.rehydrate` (or every @-mention in the transcript reaches the
model as U+FFFC), `build_prefill`/`build_tag_message` (or R-13/R-13b escaping
of group-authored text stops being pinned by its two named acceptance tests),
`deliver.build` + `Deliverer` (the ONLY caller of `adapter.send`, so R-2, R-4,
R-6 and R-18 all still apply), and `feedback.run` (a fire turn is still
something she said; omitting it makes her memory of her own speech incomplete).

Doctrine is read at `applies_to="heartbeat"` — `"participation"` for that
occasion — which is why that audience had to
exist before this module did: at `"tag"` it would carry the silence rules that
are false here, and an audience that resolved to nothing would deliver with no
persona, no constraints, and a `LeakGuard` corpus carrying nothing but the
authored sections.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from nora.agent.guards import ReplyGuard
from nora.agent.outbound import OutboundComposer
from nora.agent.prefill import (
    build_prefill,
    build_tag_message,
    escape,
)
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.doctrine.loader import DoctrineStore
from nora.domain.compute import ComputeJob
from nora.domain.events import Event, EventFactory
from nora.domain.media_join import MediaJoin
from nora.domain.models import (
    ConversationTarget,
    NormalizedMessage,
    PersonRef,
    TagContext,
    TranscriptRow,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    JobResult,
    JobStatus,
    LinkPreviewSource,
    MemoryStore,
    PlatformAdapter,
    RecordStore,
    RosterSource,
    StandingStore,
)
from nora.domain.reminders import Recurrence, Reminder, remaining_occurrences
from nora.domain.standing import (
    DEFAULT_STANDING_STORE_TIMEOUT_SECONDS,
    Standing,
)
from nora.domain.tool_call_log import ToolCallLogStore
from nora.engagement.context import StandingNotesReader
from nora.engagement.mentions import MentionRenderer
from nora.engagement.stages import deliver, feedback
from nora.engagement.stages.invoke import AgentRunner
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span, trace_attributes
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger("nora.app.fire")

# The `audience` label every `nora_silent_failures_total` this path raises
# carries — including the five inside `Deliverer`, which is why `deliver.build`
# takes it. A8 (`docs/runbook.md`) filters on `audience="tag"`, so a fire-turn
# failure recorded without this would be charged to a <1% budget whose
# denominator counts only tags.
AUDIENCE = "heartbeat"

# §8.1's four outcomes. The heartbeat derives the ROW's terminal state from the
# string (`_state_for`: only `sent` is DONE), so these are not log text —
# spelling one differently records a delivery that never happened.
SENT = "sent"
FAILED_POLICY = "failed_policy"
FAILED_TIMEOUT = "failed_timeout"
FAILED_ERROR = "failed_error"

# The participation occasion's two additions to the outcome vocabulary
# (design/dreamer-agent-loop/35 §6).
#
# `PARTICIPATION_AUDIENCE` labels every series a participation turn moves
# (silent failures, llm latency, stop reasons) so its failures are never
# charged to the heartbeat's budget, and its doctrine is read at this audience
# — the silence doctrine differs: a heartbeat turn is OBLIGED to speak, a
# participation turn is PERMITTED to.
#
# `SILENT` is the outcome the other four kinds cannot produce: the turn ran,
# the model concluded "nothing here is worth saying", and that is SUCCESS —
# the §6 healthy default, counted on `nora_participation_silence_total`, never
# on `nora_silent_failures_total`. A reminder that says nothing is broken; a
# participation turn that says nothing is working.
PARTICIPATION_AUDIENCE = "participation"
SILENT = "silent"

# The §10.6 shadow mirror's audience label: the mirror IS a send (it goes
# through `deliver.build` and every guard that carries), but it is operator
# review traffic, never room speech — its failures must not pollute the tag,
# heartbeat or participation budgets, and a mirrored "spoke" must never read
# as a live send on `nora_participation_spoke_total` (that counter moves only
# on the live path below; the mirror has its own Deliverer and never touches
# it).
SHADOW_AUDIENCE = "participation-shadow"

# How a recurrence reads in the fire header. English prose, like the rest of the
# header: the header is the SITUATION she is told about, and `doctrine` decides
# what language she answers in — the two have never been the same choice.
_EVERY = {
    # EVERY member of `Recurrence` except NONE, and it must stay that way:
    # `_repeat_clause` subscripts this bare. When #109 added the two sub-day
    # units and this table was not extended, a `minutely` reminder raised
    # KeyError inside `_fire_header` — AFTER `claim` had minted the successor
    # and BEFORE the model was called. Every occurrence claimed a row, said
    # nothing, and armed the next one: the exact «promise of eleven updates,
    # zero delivered» failure #109 exists to fix, reproduced by the fix.
    # `tests/unit/test_fire_path.py` covers the shape now.
    Recurrence.MINUTELY: "minute",
    Recurrence.HOURLY: "hour",
    Recurrence.DAILY: "day",
    Recurrence.WEEKLY: "week",
    Recurrence.MONTHLY: "month",
    Recurrence.YEARLY: "year",
}


def reminder_occasion(rows: Sequence[Reminder]) -> Event:
    """One conversation's due reminders, as the occasion they are.

    The heartbeat's rows are already grouped by `(platform, conversation_id,
    requester)` (§5.2), so `rows[0]` names all four for the whole batch.

    `body` is the requester's OWN sentence(s), joined, and that is the premise of
    the whole feature: a reminder is a deferred request, put to her at the due
    time AS a request. The set-at and due-at facts deliberately do NOT go here —
    they belong in the header, because a body that is anything other than what
    the person said breaks that premise.
    """
    row = rows[0]
    return Event(
        platform=row.platform,
        conversation_id=row.conversation_id,
        conversation_type=row.conversation_type,
        person_id=row.requester_person_id,
        body="\n".join(r.text for r in rows),
        header=lambda tagger, now: _fire_header(rows, tagger, now),
        kind="reminder",
    )


def gathering_occasion(rows: Sequence[Reminder]) -> Event:
    """A gathering's due notify, as the Event it is (DEC-139).

    `kind="gathering"` rows carry the gathering title in
    `payload["title"]` (denormalized at schedule time —
    `GatheringService._link_reminder`, design/event-abstraction/05).
    `body` is that title — the ONE fact she needs to say it back — and the
    header does the rest, exactly like `reminder_occasion` keeps the
    requester's own sentence in the body and puts the circumstances in the
    header.

    A missing `payload["title"]` is a WIRING BUG, not a runtime maybe: the
    factory raises instead of `KeyError`-ing out of the blue, and the raise
    lands in the same fail-closed path as an unregistered kind —
    `HeartbeatLoop._run_turn`'s `_fire` guard resolves the claimed row to
    FAILED (`failed_error`) immediately, never rendered as a malformed
    sentence (see design/event-abstraction/04).
    """
    row = rows[0]
    if not isinstance(row.payload, Mapping):
        raise KeyError(
            f"kind='gathering' reminder {row.id} has no payload — "
            "the row was written without its denormalized payload"
        )
    title = row.payload.get("title")
    if not isinstance(title, str):
        raise KeyError(
            f"kind='gathering' reminder {row.id} has no string "
            "payload['title'] — the row was written without its "
            "denormalized payload"
        )
    return Event(
        platform=row.platform,
        conversation_id=row.conversation_id,
        conversation_type=row.conversation_type,
        person_id=row.requester_person_id,
        body=title,
        header=lambda tagger, now: _gathering_header(title, tagger, now),
        kind="gathering",
    )


def _gathering_header(
    title: str, tagger: PersonRef | None, now: datetime,
) -> str:
    """The situation paragraph for a gathering-notify fire turn.

    Same shape as `_fire_header`: nobody called her, and that is not why
    this turn is happening. `title` arrives from the factory AFTER its
    payload guard, so it is a `str` here, never a missing key. The
    requester's name and the title are escaped because everything else this
    module puts in a prompt is (R-13).
    """
    who = escape(tagger.name if tagger is not None else "")
    safe_title = escape(str(title))
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        f"just called you. Earlier, {who} asked you to remind the room about "
        f"«{safe_title}», and that time has come — that is why this turn is "
        "happening, and it is the only reason.\n"
        f"Bring the gathering up to {who} and the room now, in your own words. "
        "Do not read the time back at them unless they asked in those terms."
    )


def compute_occasion(job: ComputeJob, result: JobResult) -> Event:
    """A finished compute job, as the occasion it is (Nora Compute spec §2)
    — the THIRD `Event`, reusing `FirePath.speak` exactly as
    `VersionAnnouncer` (DEC-97) already established as the pattern for a
    second thing built on this turn beside reminders.

    `body` is the job's own task description PLUS its result summary —
    Smith's own account of what it built (Nora Compute spec §11: this text
    passes R-18 like any other reply; the artifact bytes themselves do not).
    Nora composes the actual group-facing message from this in her own
    voice; this function only assembles what she is TOLD, not what she says.
    """
    outcome = (
        f"It finished: {result.summary}\n{result.url}"
        if result.status is JobStatus.SUCCEEDED
        else f"It failed: {result.error or 'no reason given'}"
    )
    return Event(
        platform=job.platform,
        conversation_id=job.conversation_id,
        conversation_type=job.conversation_type,
        person_id=job.requester_person_id,
        body=f"(asked earlier) {job.task}\n\n{outcome}",
        header=lambda tagger, now: _compute_header(job, tagger, now),
        kind="compute",
    )


def relay_occasion(
    *, destination: Any, requester: PersonRef, text: str, from_platform: str,
) -> Event:
    """A message somebody asked to be passed into another room — the FOURTH
    `Event` (DEC-105), following DEC-99's ruling exactly: a new way for Nora
    to speak is an occasion on this path, never a fourth send mechanism.

    That ruling is what keeps R-1b's three links intact. A relay that called
    `adapter.send` itself would bypass R-2's latch, R-4, R-6's leak scan,
    R-18's egress scan AND A9's audit-first write in one step — and the last of
    those is the one that bites quietly: the relayed message would appear in
    the destination room with no row recording it, leaving a hole in that
    room's transcript that Nora cannot see. Exactly the defect the Discord
    parser had.

    `person_id` is the REQUESTER's, so the destination's fire turn runs under
    the authority of somebody `policy.is_allowed` has already cleared for that
    room — and so §12's re-check at fire time applies to them, as it does to a
    reminder's owner.
    """
    return Event(
        platform=destination.platform,
        conversation_id=destination.primary_id,
        conversation_type="group",
        person_id=requester.person_id,
        body=text,
        speaker=requester.name,
        header=lambda tagger, now: _relay_header(requester, from_platform, tagger, now),
        kind="relay",
    )


def _relay_header(
    requester: PersonRef, from_platform: str, tagger: PersonRef | None, now: datetime,
) -> str:
    who = escape(requester.name)
    where = escape(from_platform)
    return (
        "You are in a group chat you have been reading all along. Nobody here "
        f"has just called you. {who} asked you, over on {where}, to pass "
        "something on to this room — that is why this turn is happening, and "
        "it is the only reason.\n"
        f"Pass it on, and make it unmistakable that {who} said it and not you. "
        "You are carrying somebody's message, not making a remark of your own."
    )


def lost_job_occasion(job: ComputeJob) -> Event:
    """A job whose delivery was lost across a restart — the FIFTH `Event`.

    Boot recovery cannot know whether the container finished: its handle lives
    in `LocalDockerBackend`'s in-memory registry and did not survive
    (`nora.app.compute_poller.recover`). So this must never say the job
    FAILED — that is a claim about a container nobody looked at, and it may
    well have built the thing perfectly.

    What is true, and all that is true, is that she lost track of it. That is
    R-27's `unavailable` — «I couldn't check», not «there is nothing» — and
    saying it is what closes a promise that was otherwise left open forever:
    the requester was told «I'm working on it» and, before this, heard nothing
    ever again (operator, live, 2026-08-14).
    """
    return Event(
        platform=job.platform,
        conversation_id=job.conversation_id,
        conversation_type=job.conversation_type,
        person_id=job.requester_person_id,
        body=f"(asked earlier) {job.task}",
        header=_lost_job_header,
        kind="compute_lost",
    )


def _lost_job_header(tagger: PersonRef | None, now: datetime) -> str:
    who = escape(tagger.name if tagger is not None else "")
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        f"just called you. Earlier, {who} asked you to build something and you "
        "said you were on it — then you were restarted and lost the thread of "
        "it. That is why this turn is happening, and it is the only reason.\n"
        "Own it briefly and tell them to ask again if they still want it. Do "
        "NOT say it failed and do NOT say it worked — you genuinely do not "
        "know which, and guessing either way would be a claim you cannot back."
    )


def participation_occasion(
    *, platform: str, conversation_id: str, conversation_type: str = "group",
) -> Event:
    """A room that has gone quiet, as the occasion it is
    (design/dreamer-agent-loop/35).

    The FIRST occasion with no stored row and nobody's request behind it —
    `person_id=None`, like a version announcement, because the quiescence
    trigger that fires it (`nora.app.participation.ParticipationTrigger`) owes
    the turn to no one person. Every consequence of a `None` tagger that
    `build_context` documents is accepted here as it is there.

    `body` is EMPTY on purpose, and the header says so: nothing was put to
    her. The whole basis for the determination is the injected context — the
    transcript window, standing, notes and facts that `build_context` and
    `build_prefill` assemble — which is §3.6's fast-by-construction rule: if
    the value of speaking is not visible in what is already in front of her,
    it is not worth saying.

    NOT in the reminder-derived event registry: the registry maps a due ROW's
    kind to its factory, and there is no row. The trigger calls
    `FirePath.speak` with this Event directly.
    """
    return Event(
        platform=platform,
        conversation_id=conversation_id,
        conversation_type=conversation_type,
        person_id=None,
        body="",
        header=lambda tagger, now: _participation_header(),
        kind="participation",
    )


def _participation_header() -> str:
    """The situation paragraph for a participation turn.

    Same opening shape as the other fire headers — nobody called her, and
    that is not why this turn is happening — but the reason clause differs:
    no one asked for anything, earlier or otherwise. The room went quiet and
    the floor was offered. The right to decline it is stated HERE as well as
    in `participation-conduct.md`, because the doctrine can only oblige a
    model that reads it and the header is the sentence she reads first.
    """
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        "called you — not just now, and not earlier. The conversation has gone "
        "quiet, and this turn is the offer of the floor, nothing more.\n"
        "The tag_message below is deliberately empty: nothing was put to you. "
        "Decide from what is already in front of you — the transcript above, "
        "and whatever standing and notes came with it. If speaking "
        "would add something the room can use, say it — once, briefly. If "
        "not, stay silent: silence is a complete answer here, not a failure."
    )


def _compute_header(job: ComputeJob, tagger: PersonRef | None, now: datetime) -> str:
    who = escape(tagger.name if tagger is not None else "")
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        f"just called you. Earlier, {who} asked you to build something, and "
        "the job has now finished (or failed) — that is why this turn is "
        "happening, and it is the only reason.\n"
        "Tell them what you built (or what went wrong), in your own words, "
        "including the link if there is one. Do not just repeat the raw "
        "summary verbatim — say it the way you'd actually tell a friend."
    )


class FirePath:
    """One fire turn, end to end. Every collaborator arrives through `__init__`.

    `clock` is here for the same reason it is on `HeartbeatLoop`: the header
    states when the reminder was set and when it came due, and a module that
    read the wall clock itself could not be tested at a fixed instant.
    """

    def __init__(
        self,
        *,
        persons: Persons,
        records: RecordStore,
        memory: MemoryStore,
        doctrine_store: DoctrineStore,
        metrics: Metrics,
        runner: AgentRunner,
        adapters: Mapping[str, PlatformAdapter],
        registry: ToolRegistry,
        canary: str,
        display_name: str,
        ai_peer: str,
        context_window: int,
        limits: dict[str, Any],
        timeout_seconds: float,
        clock: Callable[[], datetime],
        media_join: MediaJoin | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        preview_source: LinkPreviewSource | None = None,
        llm_state: Any = None,
        tool_call_log_store: ToolCallLogStore | None = None,
        event_registry: Mapping[str, EventFactory] | None = None,
        standing: StandingStore | None = None,
        standing_store_timeout_seconds: float = DEFAULT_STANDING_STORE_TIMEOUT_SECONDS,
        notes: StandingNotesReader | None = None,
        policy: Policy | None = None,
        participation_runner: AgentRunner | None = None,
        shadow_channel: str | None = None,
        shadow_platform: str = "discord",
    ) -> None:
        # S5 (15-roadmap §6.3): `<standing>` renders on the fire path too.
        #
        # It was cited here as "D5" until 2026-08-27, which is the exact
        # mistake CLAUDE.md keeps a war-story for: the number came off the
        # PLAN's local `Decisions needed from the operator` table (D1-D6),
        # and the roadmap relabelled those S1-S6 when it CLOSED them. The
        # global D5 is `design/09-decision-index.md:36` (identity is config,
        # never hardcoded) and, in this very bundle,
        # `00-overview.md:134` (Phase A gains tools). Anyone following the
        # old citation landed on a Dreamer extraction decision about tool
        # grants. `S1` two paragraphs down was right; its sibling was not.
        #
        # `build_prefill` is
        # shared and parameterised only by `header=`, so SUPPRESSING the block
        # here is the special case that would need justifying — and it was
        # suppressed silently until 2026-08-27, simply because `build_context`
        # below never passed `standing=` and the field defaults to `None`.
        #
        # It matters most on exactly this path: a fire turn speaks into a room
        # UNPROMPTED, so «where had this conversation got to» is the context she
        # is least likely to have any other way — there is no tagging message to
        # infer it from. `None` when `[standing] enabled = false`, the shipped
        # default, which renders no block at all.
        #
        # This path READS the position and never writes one. That asymmetry is
        # not a choice: S1 (fire turns debrief too) is blocked on the fire path
        # retaining a `TagTrace` at all — `speak` discards the one
        # `AgentRunner.invoke` hands back, a Phase 1 deferral flagged in that
        # method — and there is nothing to fold without it.
        self._standing = standing
        # BOUNDED, for the reason the debrief stage's own store calls are:
        # the heartbeat holds this conversation's slot around the whole fire
        # turn (`heartbeat.py` try_acquire -> release_and_dispatch), and
        # `create_sql_engine` sets no `command_timeout`, so an unbounded read
        # here wedges the conversation exactly as the unbounded read in
        # `debrief.run` did. That defect was fixed and then RE-CREATED here in
        # the same change; this is the second half of the same fix.
        self._standing_store_timeout_seconds = standing_store_timeout_seconds
        # The note tier on the fire path (design/dreamer-agent-loop/35,
        # Phase 2) — S5's exact precedent, one tier later. A participation
        # determination grounds on «transcript + standing/notes», which was a
        # lie as shipped: Phase 1's own header told the model "whatever
        # standing and notes came with it" while `build_context` left the
        # field at its dataclass default, so no `<notes>` block ever rendered
        # on ANY fire occasion. Wired for every occasion kind, not gated to
        # participation: one `build_context` shape, no per-occasion special
        # case, and the tier is default-disabled so no shipped prompt changes
        # until an operator enables it.
        #
        # `policy` is here for exactly one read: the tier is keyed on
        # `Conversation.workspace` (DEC-105), so scoping it needs the same
        # `Policy` instance the assembler holds — a fire path with its own
        # idea of a room's workspace would render another social unit's notes
        # into this one. `None` keeps the tier inert, the pre-wiring shape
        # every test construction site has.
        self._notes = notes
        self._policy = policy
        self._persons = persons
        self._records = records
        # The shared media join (issue #116). A fire turn renders the SAME
        # transcript block through the SAME prefill as a tag, so leaving it out
        # here would reintroduce the two-consumer split on exactly the path
        # nobody is watching when it fires — the argument the
        # `name_transcript` projection below already makes.
        self._media_join = media_join
        self._memory = memory
        self._doctrine_store = doctrine_store
        self._metrics = metrics
        self._runner = runner
        # Platform -> adapter, for the reason `EngagementPipeline` holds the
        # same map: `design/discord-integration/02` makes fire-path eligibility
        # explicitly NOT platform-specific, so a reminder set in a Discord
        # channel fires into that channel. A single adapter here would have
        # spoken every occasion into Signal regardless of where it was owed.
        self._adapters = adapters
        # Kind -> factory, the dispatch point for reminder-derived Events
        # (DEC-139). Defaults to the pre-DEC-139 hardcoded call so every
        # construction site that predates the registry behaves identically;
        # the composition root wires the real registry.
        self._event_registry = dict(
            event_registry if event_registry is not None else {"reminder": reminder_occasion}
        )
        self._registry = registry
        self._canary = canary
        self._display_name = display_name
        self._ai_peer = ai_peer
        self._context_window = context_window
        self._limits = limits
        # `[heartbeat] timeout_seconds`, NOT `[llm] timeout_seconds`. A fire
        # turn has nobody waiting on it, so the tag path's 60s — chosen because
        # a human is staring at a group chat — is the wrong bound here.
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        # Optional for the reason it is optional on the pipeline: `Room` reports
        # an absent source as UNAVAILABLE rather than as an empty room, so a
        # harness degrades honestly instead of answering "nobody is here" (R-5).
        # Platform -> roster source. ONE source used to serve every platform,
        # which meant a Discord room was looked up in signal-cli: the daemon
        # does not know that conversation id, so the roster came back
        # `available=False` and every room-scoped tool — `where_is` included —
        # answered «I could not look». Correct fail-closed behaviour (R-5) on
        # top of a wiring gap, which is the shape that hides longest, because
        # nothing errors and the honest refusal looks like the feature working.
        #
        # A MISSING platform here degrades rather than raising, unlike
        # `_adapter_for`: `Room` already documents an absent source as
        # UNAVAILABLE (never as an empty room), so the honest answer is already
        # built, and an offline/eval composition legitimately has no roster at
        # all. Delivering to the wrong platform is catastrophic; failing to
        # read a roster is merely blind, and blind-but-saying-so is allowed.
        self._rosters: Mapping[str, RosterSource] = rosters or {}
        # The link-preview fetcher, `None` unless `[preview].enabled` — see the
        # engagement pipeline's identical field for why this one defaults while
        # `deliver.build`'s `composer` does not.
        self._preview_source = preview_source
        self._llm_state = llm_state
        # Same tool-recall-log store the tag path threads (nora.engagement.
        # pipeline.EngagementPipeline._tool_call_log_store) — `None` keeps
        # this path exactly as inert as it already is for a deployment that
        # has not wired one.
        self._tool_call_log_store = tool_call_log_store
        # The participation occasion's runner (design/dreamer-agent-loop/35):
        # the SAME AgentRunner shape composed with `tool_choice="auto"` —
        # the determination may end in silence, which `required` (the tag
        # runner's pin) makes inexpressible. `None` whenever
        # `[participation] enabled = false`; `_invoke` selects it by occasion
        # kind, and a participation turn arriving with no runner composed is
        # a wiring fault, not a turn to run on the tag runner.
        self._participation_runner = participation_runner
        # The §10.6 shadow mirror. `shadow_channel` None means shadow mode is
        # OFF (the shipped default — `[participation] shadow = false`), and
        # `_mirror` returns without doing anything; set, every participation
        # determination — speak AND silence — is mirrored to that channel on
        # `shadow_platform`, through the same `deliver.build`/guards as any
        # send, under `SHADOW_AUDIENCE`. The composition root wires it only
        # when `enabled` AND `shadow` are both on, so a mirrored determination
        # always cleared the room opt-in first.
        self._shadow_channel = shadow_channel
        self._shadow_platform = shadow_platform

    async def build_context(self, occasion: Event) -> TagContext:
        """§6.4's table, assembled HERE rather than by `ContextAssembler`.

        **This is the trap the whole section exists for.** The synthetic message
        carries a blank `sender_id` on purpose — that is what makes §5.5's "no
        reactions" hold by construction. But `TranscriptInjection.assemble`
        resolves the tagger as `persons.resolve(platform, msg.sender_id)`, and a
        blank id resolves to `None`. Every one of these then follows, silently,
        with every gate still green:

        * `ctx.tagger is None`, so `peer_card` is `()` — she remembers nothing
          about the person she is answering
        * `Room(tagger=None).scope()` returns UNAVAILABLE, so every room-scoped
          tool goes dark
        * `search_memory` refuses outright
        * §12's `tagger is None -> refuse` gate fires on EVERY fire turn, for a
          reason that has nothing to do with trust

        So the tagger comes from the OCCASION's `person_id` — for a reminder,
        the id the tick already screened `policy.is_allowed` against — through
        `Persons.by_person`, and never from the message.

        An occasion with `person_id=None` (a version announcement) therefore has
        `tagger is None` DELIBERATELY, and every consequence above is accepted
        rather than escaped: nobody asked, so there is no peer card to read and
        no person whose room membership scopes a lookup. What makes that safe is
        that `speak` skips §12's refusal only for such an occasion, and that the
        tools which go dark are exactly the ones an announcement has no use
        for.

        `sender_name` has the same shape and is the second half of the same
        trap: `Persons.display_name(msg)` reads `msg.sender_id` and would
        return `""` here. It comes off the `PersonRef` too.

        Async because both its sources are: `RecordStore.recent` and
        `MemoryStore.peer_card`. A synchronous signature would push an
        implementer toward an empty transcript and an empty card — a fire turn
        with no context at all, and nothing anywhere saying so.
        """
        # `.get`, not `[...]`: identity.toml loads once at startup and a row may
        # be thirty days old, so the person it names may have left the file. A
        # KeyError here would surface as `failed_error` with a traceback; `speak`
        # refuses it as `failed_policy` with a sentence instead. `None` for an
        # occasion held on nobody's behalf, which is not an error at all.
        ref = (
            self._persons.by_person.get(occasion.person_id)
            if occasion.person_id is not None else None
        )

        transcript: tuple[TranscriptRow, ...] = ()
        try:
            transcript = tuple(await self._records.recent(
                conversation_id=occasion.conversation_id, limit=self._context_window,
            ))
        except Exception:
            # `TranscriptInjection`'s behaviour, deliberately copied: a thinner
            # turn beats no turn (R-5-adjacent). The counter is what says the
            # turn ran blind.
            log.exception("fire context read failed; continuing with an empty transcript")
            self._metrics.inc(
                "nora_errors_total", stage="fire_context", platform=occasion.platform,
            )

        # The same one-name-space projection the tag path applies (DEC-80). A fire
        # turn renders the SAME transcript block through the SAME prefill, so
        # leaving it out here would reintroduce the two-name-space bug on exactly
        # the path nobody is watching when it fires.
        transcript = self._persons.name_transcript(transcript, platform=occasion.platform)
        if self._media_join is not None:
            transcript = await self._media_join.enrich(transcript)

        card: tuple[str, ...] = ()
        if ref is not None:
            try:
                card = tuple(await self._memory.peer_card(peer_id=ref.honcho_peer_id))
            except Exception:
                # Guarded where `TranscriptInjection`'s equivalent is not, and
                # the asymmetry is §8.2: at-most-once means a raise here writes
                # `failed_error` and the reminder is GONE. The tag path's
                # equivalent costs somebody a re-tag. The shipped
                # `HonchoClient.peer_card` returns `[]` on failure, so nothing
                # reaches this today — it is here because the cost of the day
                # something does is a reminder nobody can get back.
                # Its OWN stage, not `fire_context`: an operator asking which
                # half of the context went blind must not have to guess.
                log.exception("fire peer_card read failed; continuing without it")
                self._metrics.inc(
                    "nora_errors_total", stage="fire_peer_card", platform=occasion.platform,
                )

        # The workspace-keyed tier resolves the workspace ONCE per turn, and
        # the read is skipped entirely when it is not wired (the dormant
        # default must not count a gap it does not have).
        workspace: str | None = None
        if self._notes is not None:
            workspace = self._workspace_for(occasion)

        return TagContext(
            tag_id=uuid.uuid4().hex,
            message=_synthetic_message(occasion, ref),
            tagger=ref,
            transcript=transcript,
            peer_card=card,
            started_at=time.perf_counter(),
            standing=await self._read_standing(occasion),
            notes=await self._read_notes(occasion, workspace),
        )

    def _workspace_for(self, occasion: Event) -> str | None:
        """The workspace this occasion's room belongs to, or `None`.

        The workspace-keyed tier scopes through the SAME `Policy` read
        `TranscriptInjection._workspace_of` uses (DEC-105): two readers each
        carrying their own idea of a room's workspace disagree the first time
        a deployment runs two social units.

        `None` — a store wired but no `Policy` or no `Conversation` for the
        room — is a WIRING GAP, not an empty tier: guessing a workspace would
        render another unit's notes into this room. Counted, because a
        block missing for the life of a deployment with nothing saying so is
        how `_peer_card`'s `nora_peer_card_empty_total` lesson was bought.
        """
        try:
            convo = (
                self._policy.conversation(occasion.platform, occasion.conversation_id)
                if self._policy is not None
                else None
            )
        except Exception:
            # Fail-soft like the two sibling reads (`_read_notes`,
            # `_read_standing`): a Policy read that raises must
            # not turn a deliverable reminder into a silent no-send (Claude
            # Sonnet audit 2026-09-05 MINOR). Counted, because a block missing
            # for the life of a deployment with nothing saying so is how
            # `_peer_card`'s `nora_peer_card_empty_total` lesson was bought.
            log.exception(
                "policy conversation read failed for %s/%s — the note "
                "tier cannot be scoped to a workspace; continuing without it",
                occasion.platform, occasion.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="fire_workspace",
                platform=occasion.platform,
            )
            return None
        if convo is None:
            log.warning(
                "no policy conversation for %s/%s — the note tier cannot "
                "be scoped to a workspace; continuing without it",
                occasion.platform, occasion.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="fire_workspace",
                platform=occasion.platform,
            )
            return None
        return convo.workspace

    async def _read_notes(
        self, occasion: Event, workspace: str | None,
    ) -> tuple[Note, ...]:
        """The standing note index, `TranscriptInjection._notes` mirrored.

        Fail-SOFT and COUNTED, the same three states: no store is the dormant
        default (not an error, not counted); no workspace is the wiring gap
        above (counted there, once); a FAILED read is neither — a thinner
        prompt beats a reminder that does not fire, but a note index that
        exists and could not be fetched reported as «there are none» is the
        `unavailable`-as-`nothing_found` substitution R-27 exists to prevent.
        """
        if self._notes is None or workspace is None:
            return ()
        try:
            return tuple(await self._notes.standing(workspace=workspace))
        except Exception:
            log.exception("fire note-index read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="fire_notes",
                platform=occasion.platform,
            )
            return ()

    async def _read_standing(self, occasion: Event) -> Standing | None:
        """This conversation's position, or `None` (S5).

        Fail-SOFT and COUNTED, the same three states
        `TranscriptInjection._standing` distinguishes: no store is the dormant
        default and is not an error; no row is a conversation never folded; a
        FAILED read is neither, and is counted, because a position that exists
        and could not be fetched reported as «there is none» is the
        `unavailable`-as-`nothing_found` substitution R-27 exists to prevent.

        A thinner prompt beats a reminder that does not fire.
        """
        if self._standing is None:
            return None
        try:
            return await asyncio.wait_for(
                self._standing.read(
                    platform=occasion.platform,
                    conversation_id=occasion.conversation_id,
                ),
                timeout=self._standing_store_timeout_seconds,
            )
        except Exception:
            log.exception("fire standing read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="fire_standing",
                platform=occasion.platform,
            )
            return None

    async def run(self, rows: Sequence[Reminder]) -> str:
        """The heartbeat's `fire` collaborator: due rows in, §8.1 outcome out.

        Dispatch by KIND, through the reminder-derived registry
        (DEC-139, design/event-abstraction/04): `rows[0].kind` selects the
        factory that renders the batch's Event. Today that is
        `"reminder"` → `reminder_occasion` and `"gathering"` →
        `gathering_occasion`; an UNREGISTERED kind raises inside this call,
        before `speak`, and `HeartbeatLoop._run_turn`'s `_fire` guard
        resolves the claimed rows to FAILED (`failed_error`) immediately —
        never rendered as a malformed sentence, never left FIRING for boot
        `recover()`.

        The registry defaults to `{"reminder": reminder_occasion}` so a
        FirePath built without one (every pre-DEC-139 construction site)
        behaves byte-identically to the hardcoded call it replaces; the
        composition root wires the real registry.

        It stays a separate method rather than becoming a lambda in the
        composition root for two reasons. It keeps `HeartbeatLoop._fire` a
        BOUND METHOD of the real fire path — which is what
        `tests/test_composition.py::test_the_heartbeat_is_wired_to_the_real_fire_path`
        asserts, and a lambda would satisfy that test while wrapping anything
        at all. And it keeps the event factories assembled HERE, beside the
        header prose they need, rather than in a module that has no business
        knowing what a fire turn is (`nora.app.heartbeat`'s own docstring).
        """
        factory = self._event_registry.get(rows[0].kind)
        if factory is None:
            raise KeyError(
                f"no event factory registered for kind={rows[0].kind!r} "
                f"(reminder {rows[0].id}) — wired: {sorted(self._event_registry)}"
            )
        return await self.speak(factory(rows))

    async def speak(self, occasion: Event, *, send_permitted: bool = True) -> str:
        """One turn, and the §8.1 outcome its caller records.

        **It does not call `invoke.run` or `deliver.check_outcome`** (§6.2).
        Not for metric isolation — that was tried twice and undercounted twice,
        which is why `audience` exists — but because it needs its own timeout
        (`[heartbeat] timeout_seconds`) and its own outcome mapping: both of
        those functions answer `bool`, and a `bool` cannot distinguish
        `failed_timeout` from `failed_error`, which is precisely the
        distinction the row has to record. It reimplements the one side effect
        `check_outcome` has that is not accounting — the `llm_state.last_success_at`
        touch — because a successful fire turn IS a successful invocation, and
        omitting it would let a healthy process report a stale LLM to `/health`.

        Only `sent` is DONE, and `sent` means the `Deliverer` actually
        delivered. Anything else is a turn the group never heard — a reminder
        that did not arrive, or a version nobody was told about. The
        participation occasion (kind="participation") is the exception that
        proves the reading: for it "the group never heard" is the HEALTHY
        default, so its quiet turn answers `SILENT`, not a failure string.

        `send_permitted` is the participation occasion's SEND cooldown
        (design/dreamer-agent-loop/35 §10.3), threaded into `deliver.build`;
        every other occasion leaves it True. The trigger decides and this
        method enforces — the two are separate so that a trigger bug cannot
        both mistime the evaluation and skip the hold.
        """
        ctx = await self.build_context(occasion)
        # Every log line for the rest of this turn — across every stage it calls
        # into — carries the tag id without naming it (nora.obs.logging's
        # contextvar bridge), so a fire turn is greppable exactly like a tag.
        bind_tag_id(ctx.tag_id)
        # TRUE SHADOW (design/dreamer-agent-loop/35 §10.6, Miles decision
        # 2026-09-05): while the mirror is wired, a participation turn NEVER
        # live-sends — `send_permitted` is forced False so the Deliverer holds
        # the message at the last inch and `_turn` mirrors the draft instead.
        # The operator reviews determinations on the shadow channel; the room
        # hears nothing until the flip (shadow = false). This is what makes
        # shadow a review surface rather than a live send with a copy.
        if occasion.kind == "participation" and self._shadow_channel is not None:
            send_permitted = False
        try:
            # THE ROOT SPAN, for EVERY occasion (#111). It existed for
            # `participation` alone (design/dreamer-agent-loop/35 §9, Miles
            # 2026-09-05: "include langfuse and jaeger — otherwise how can I
            # analyse with observability"), and every other kind — reminder,
            # gathering, compute, relay, lost_job, version — took the bare
            # `_turn` path and produced a trace with NO ROOT: sibling spans, no
            # name, none of the `nora.*` attributes. That is not one trace with
            # gaps, it is N traces, and the instrument catalogue's first rule is
            # one root per entry point.
            #
            # WHAT IT COST, measured: trace 1f0df2ee (2026-09-06 13:10Z) is the
            # F1 reminder firing. It ran 119.5s against `[heartbeat]
            # timeout_seconds = 120` and resolved `failed_timeout` — the room
            # got nothing. That outcome is in Postgres and NOWHERE in the trace,
            # because there was no span to annotate it onto. The only reason it
            # was diagnosable at all was a manual database read.
            #
            # An unprompted path is where this matters MOST: nobody is waiting
            # on it, so a silent failure has no human to notice it.
            #
            # `generation` kind on every occasion, not just participation: each
            # of them contains a real model call, which is what the Langfuse
            # sampler selects on. `nora.outcome` is annotated on the way out so
            # the §8.1 string an occasion answers with — `sent`,
            # `failed_timeout`, `failed_policy`, `failed_error`, `SILENT` — is
            # readable in the trace rather than only in the row.
            with span(
                occasion.kind,
                **{TRACE_KIND: "generation",
                   "nora.platform": occasion.platform,
                   "nora.conversation_id": occasion.conversation_id,
                   "nora.occasion": occasion.kind,
                   # Meaningful only for participation, and False elsewhere
                   # rather than absent: an attribute that appears on some roots
                   # and not others cannot be filtered on.
                   "nora.shadow": (
                       occasion.kind == "participation"
                       and self._shadow_channel is not None
                   )},
            ):
                outcome = await self._turn(
                    occasion, ctx, send_permitted=send_permitted,
                )
                annotate(**{"nora.outcome": outcome})
                return outcome
        finally:
            clear_tag_id()

    def _adapter_for(self, platform: str) -> PlatformAdapter:
        """The adapter this occasion is owed on, or a hard failure.

        Raises rather than falling back to any wired adapter, the same
        judgement `EngagementPipeline._adapter_for` makes and for the same
        reason: speaking a reminder into a room on another platform is worse
        than not speaking it. Raised inside `_turn`, whose outer guard turns it
        into `failed_error` plus `nora_silent_failures_total{reason=
        "handler_error"}` — so the wiring fault is observable rather than
        silent (R-5), and the row is not left claiming it fired.
        """
        try:
            return self._adapters[platform]
        except KeyError:
            raise KeyError(
                f"no platform adapter is wired for {platform!r} — "
                f"wired: {sorted(self._adapters)}"
            ) from None

    async def _turn(
        self, occasion: Event, ctx: TagContext, *, send_permitted: bool = True,
    ) -> str:
        """One turn's stages, its last-resort guard, and DEC-42's observable.

        The `except` is the fire path's counterpart to
        `EngagementPipeline._run_admitted`'s outer guard — §6.2 enumerated nine
        sites that must carry `audience`, all of them on the tag path, and this
        is the tenth.
        Without it every stage between `resolve_identity` and
        `build_tag_message` could fault straight past `nora_silent_failures_total`
        into the heartbeat's `failed_error` — the same string an ordinary model
        error writes, so a total wiring fault and a bad model call were one
        reading. `handler_error` is R-5's last-resort signal and this is where
        the fire path raises it.

        `Exception`, not the tag path's `BaseException`, and that is the one
        deliberate divergence. The tag path is a spawned turn nobody awaits, so
        swallowing everything is what makes its silence guarantee unconditional.
        This one is awaited by `HeartbeatLoop`, whose `run()` loop is cancelled
        at shutdown — absorbing a `CancelledError` here would answer
        `failed_error`, write a terminal row, and swallow the cancellation of
        the loop itself.

        The `finally` is DEC-42/R-25 on this path: a turn that sent SOME of its
        messages and then stopped is NOT silent — `delivered` is True, the
        outcome is `sent`, the row is DONE — so nothing R-23 built can see it.
        It runs in a `finally` for the tag path's reason: that is the one point
        every exit path converges on, the cap-cut loop, the timeout, the
        `llm_error` and the guard above alike.
        """
        # The three the `finally` reads. LOCALS, never attributes: `FirePath` is
        # built once per process and outlives every turn, so state parked on
        # `self` would let one turn's stop reason describe the next one's
        # delivery — the shape `Handler._guards` had.
        handle = None
        stop_reason: str | None = None
        completed = False
        # The participation occasion carries its OWN audience (design/
        # dreamer-agent-loop/35 §6): its doctrine is the may-speak rule set,
        # its failures are never charged to the heartbeat's budget, and its
        # quiet turn is a success (`SILENT`), never a `failed_*`.
        participation = occasion.kind == "participation"
        audience = PARTICIPATION_AUDIENCE if participation else AUDIENCE
        try:
            tagger = ctx.tagger
            if tagger is None and occasion.person_id is not None:
                # §12: only a mapped person may hold a reminder, re-checked here
                # for the same reason policy is (§5.1) — identity.toml is read
                # once at startup and this row may be thirty days old.
                # `failed_policy`, not `failed_error`: nothing went wrong, the
                # grant went away.
                #
                # `and occasion.person_id is not None` is the whole difference an
                # occasion held on NOBODY's behalf makes. Without that clause a
                # version announcement — which names no requester by design —
                # would refuse itself on a rule about a grant it never had, and
                # the failure would read as a revoked person. An occasion that
                # DOES name someone still refuses, unchanged.
                log.error(
                    "%s turn not fired: %s is no longer in identity.toml",
                    occasion.kind, occasion.person_id,
                )
                return FAILED_POLICY

            identity = await self._adapter_for(occasion.platform).resolve_identity()
            self_ids = frozenset(getattr(identity, "aliases", ()))
            # Without this every @-mention in the transcript — including mentions
            # of Nora herself — reaches the model as U+FFFC. Same placement as the
            # tag path: on the assembled context, never at ingest (the audit
            # stores what arrived) and never in the prefill (which may not import
            # nora.platform).
            ctx = MentionRenderer(
                persons=self._persons, display_name=self._display_name,
                self_ids=self_ids, metrics=self._metrics,
            ).rehydrate(ctx)

            doctrine = self._doctrine_store.get(applies_to=audience)
            log.info(
                "fire turn start (tag_id=%s kind=%s conversation_id=%s "
                "person_id=%s doctrine_sha256=%s)",
                ctx.tag_id, occasion.kind, occasion.conversation_id,
                occasion.person_id or "-", doctrine.sha256,
            )

            handle = deliver.build(
                store=self._records, adapter=self._adapter_for(occasion.platform),
                target=ctx.message.target(),
                doctrine=doctrine, canary=self._canary, tag_id=ctx.tag_id,
                # Nora's own id ON THIS PLATFORM, from the platform itself
                # (`resolve_identity`, resolved just above) — never a string an
                # operator typed into config.toml. A hardcoded value is one
                # deployment's guess about who Nora is; the platform is the only
                # authority on that, and with more than one platform wired a
                # single configured string is necessarily wrong for all but one
                # of them (DEC-102).
                nora_sender_id=identity.primary,
                conversation_id=occasion.conversation_id,
                # `allowed_mcp` left at its default (empty): `FirePath` has no
                # `Policy`/allowlist collaborator wired in (unlike
                # `EngagementPipeline`, which threads `self._policy` — see
                # final-review Finding 1), so there is no per-conversation MCP
                # scope available here to pass. Empty is the safe default —
                # this mirrors the `.build()` call below, which likewise never
                # passes `allowed_mcp` and so never appends an MCP tool to a
                # fire turn.
                budgets=self._registry.budgets(),
                terminal=self._registry.terminal_tools(), metrics=self._metrics,
                audience=audience,
                cacheable_tools=self._registry.cacheable_tools(),
                tool_call_log_store=self._tool_call_log_store,
                # The participation SEND cooldown (§10.3). True on every other
                # occasion — the heartbeat has no cooldown, and this keyword's
                # whole reason for travelling through `deliver.build` rather
                # than sitting in the trigger alone is that the trigger decides
                # and the `Deliverer` enforces, at the last inch.
                send_permitted=send_permitted,
                # A fire turn is still a message into a real group, so it gets
                # the same outbound decoration the tag path does — the same
                # `roster_source`, the same `Persons`. Threaded rather than
                # left `None` for the reason this module reuses `deliver.build`
                # at all: a reminder answered in her own voice that cannot tag
                # the person it is FOR ("@grant 你話要我提你…") is the one
                # shape where a mention is most obviously wanted.
                composer=OutboundComposer(
                    platform=occasion.platform, conversation_id=occasion.conversation_id,
                    persons=self._persons, metrics=self._metrics,
                    roster_source=self._rosters.get(occasion.platform),
                    preview_source=self._preview_source,
                ),
            )
            room = Room(
                platform=occasion.platform, conversation_id=occasion.conversation_id,
                roster_source=self._rosters.get(occasion.platform), persons=self._persons,
                # `None` for an occasion nobody asked for, which is what makes
                # `Room.scope()` report UNAVAILABLE there rather than a room —
                # the honest state (R-21), not a degraded one.
                tagger=tagger, self_ids=self_ids,
            )
            tools = self._registry.build(
                store=self._records, conversation_id=occasion.conversation_id,
                conversation_type=occasion.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer, room=room,
                question=ctx.message.body, context_window=self._context_window,
                metrics=self._metrics,
                # DEC-146: the same window `build_prefill` renders below. A fire
                # turn reads a real room's real transcript, so `who_said` works
                # here exactly as it does on a tag — and an occasion nobody asked
                # for is precisely where an unchecked attribution would be worst.
                transcript=ctx.transcript,
                # The one thing that tells a tool this turn was not a tag. Task
                # 6's `remind` refuses on it: a reminder that can set a reminder
                # is an unbounded loop, and the conversation it would fire into
                # is this one.
                fire_turn=True,
                # DEC-89: the same carrier `handle`'s `Deliverer` holds. A fire
                # turn can absolutely recommend a place («你話要我提你訂枱») and
                # is a message into a real group, so it gets the link behaviour
                # for the same reason it gets the `OutboundComposer` above.
                links=handle.links,
            )
            now = self._clock().astimezone()
            pre_fill = build_prefill(
                ctx, display_name=self._display_name,
                context_window=self._context_window,
                now=now, header=occasion.header(tagger, now),
            )
            # `tagger.name`, NOT `Persons.display_name(ctx.message)` — that reads
            # `sender_id`, which is blank here, and would attribute the request to
            # nobody. Same trap as `build_context`'s `sender_name`. An occasion
            # with no person falls back to the name IT gives its author
            # (`Event.speaker`), because an unattributed `<tag_message>` is
            # unsourced text in the prompt — the one thing R-13's block structure
            # exists to prevent.
            tag_message = build_tag_message(
                ctx,
                tagger_name=tagger.name if tagger is not None else occasion.speaker,
                display_name=self._display_name,
            )

            failure, stop_reason, final_text = await self._invoke(
                doctrine=doctrine, tools=tools, pre_fill=pre_fill,
                guard=handle.guard, tool_call_log=handle.tool_call_log,
                tag_message=tag_message, ctx=ctx,
                platform=occasion.platform, deliverer=handle.deliverer,
                participation=participation, audience=audience,
            )
            # `truncation_reason`'s "the turn reached the end of its try block",
            # decided HERE rather than at the end of it. The tag path can leave
            # it to the last line because a failed invocation returns early
            # there; a fire turn that timed out AFTER delivering still answers
            # `sent` (§8.1: the outcome is `sent` iff the group heard something),
            # so it runs on to the end of this block, and a `completed = True`
            # down there would tell `truncation_reason` the invocation finished
            # when it was abandoned mid-reply — the one case this counter exists
            # for.
            completed = failure is None
            if not handle.deliverer.delivered:
                if failure is not None:
                    return failure
                if participation:
                    # SILENT, not FAILED_ERROR: a participation turn that
                    # delivered nothing is the HEALTHY default (§6), whether
                    # the model declined the floor or the send cooldown held
                    # its message — `_invoke` has already filed the turn under
                    # `nora_participation_silence_total`, or as an ordinary
                    # silent failure if a budget cap cut it off.
                    await self._mirror(
                        occasion, ctx, doctrine, verdict="silent",
                        texts=handle.deliverer.held_texts or (
                            (final_text,) if final_text.strip() else ()
                        ),
                        held=bool(handle.deliverer.held_texts),
                        stop_reason=stop_reason,
                    )
                    return SILENT
                return FAILED_ERROR

            # A delivered reply implies a successful invocation — the touch
            # `deliver.check_outcome` performs, reimplemented rather than
            # inherited (§6.2). Without it a process whose only recent speech was
            # a reminder reports a stale LLM on `GET /health`.
            if self._llm_state is not None:
                self._llm_state.last_success_at = time.time()
            if participation:
                self._metrics.inc(
                    "nora_participation_spoke_total", platform=occasion.platform,
                )
                await self._mirror(
                    occasion, ctx, doctrine, verdict="spoke",
                    texts=handle.deliverer.sent_texts, held=False,
                    stop_reason=stop_reason,
                )
            await self._feed(
                handle.deliverer.sent_texts, occasion.conversation_id,
                occasion.platform,
            )
            return SENT
        except Exception:
            log.exception("fire turn failed (tag=%s); staying silent", ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="handler_error",
                platform=occasion.platform, audience=audience,
            )
            # Whatever the invocation reported, this turn did NOT reach its end —
            # which is what makes `pipeline_error` reachable for a fault raised
            # after some messages had already gone out.
            completed = False
            return FAILED_ERROR
        finally:
            # The participation root span's stop_reason (no-op on every other
            # occasion — no span is current, and annotate on a non-recording
            # span costs nothing). The disambiguating signal the eval gate
            # reads: a SILENT determination that ended in invocation_timeout
            # is an ERROR, not a decline.
            if stop_reason is not None:
                annotate(**{"nora.stop_reason": stop_reason})
            messages = handle.deliverer.delivered_count if handle is not None else 0
            if messages:
                reason = deliver.truncation_reason(
                    stop_reason=stop_reason, completed=completed,
                )
                if reason is not None:
                    log.warning(
                        "partial_reply: %d %s message(s) sent, then %s "
                        "(tag=%s)", messages, occasion.kind, reason, ctx.tag_id,
                    )
                    self._metrics.inc(
                        "nora_partial_replies_total", reason=reason,
                        platform=occasion.platform,
                    )

    async def _invoke(
        self,
        *,
        doctrine: Any,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: ReplyGuard,
        tool_call_log: Any = None,
        tag_message: str,
        ctx: TagContext,
        platform: str,
        deliverer: Any,
        participation: bool = False,
        audience: str = AUDIENCE,
    ) -> tuple[str | None, str | None, str]:
        """The model call, with the heartbeat's own timeout and its own
        accounting. Returns `(failure, stop_reason, final_text)`: the §8.1
        failure name — or None if the loop finished — how the loop ENDED, and
        the prose the model wrote (DEC-93).

        A `None` failure does NOT mean she spoke — `_turn` asks the `Deliverer`
        that. It means the invocation completed, which is a different fact, and
        conflating them is what `deliver.check_outcome`'s `stop_reason`
        parameter exists to prevent (R-23).

        `stop_reason` is handed back rather than only counted here for the
        reason `invoke.run` hands it back: `truncation_reason` needs it, and
        `None` (the invocation never returned) means something different from
        `"limit_turns"` — a distinction `_turn`'s `finally` cannot recover
        afterwards.

        `final_text` travels for the §10.6 shadow mirror: on a participation
        turn it is the DECLINE draft — what she would have said and why she
        stayed silent — which the mirror posts for review. The DEC-93 recovery
        branch below never fires on that occasion, so there it is only ever a
        draft, never a delivered message.
        """
        started = time.perf_counter()
        stop_reason: str | None = None
        # DEC-93: the prose the model wrote instead of calling `send_reply`.
        # A fire turn has nobody watching a 👀, but it has the same tool-choice
        # exposure — the route accepts `required` and ignores it — and a
        # reminder that fires into silence is the one failure the heartbeat
        # exists to prevent.
        final_text = ""
        failure: str | None = None
        # The participation occasion runs on its OWN runner — the composition
        # root builds it with `tool_choice="auto"`, because a determination
        # that ends in silence must be expressible and the tag runner's
        # `required` pin would demand a send. `None` means the occasion was
        # admitted with no runner composed — a wiring fault, raised (and so
        # counted as `handler_error` by `_turn`), never quietly downgraded to
        # the tag runner.
        runner = self._runner
        if participation:
            if self._participation_runner is None:
                raise RuntimeError(
                    "a participation occasion arrived but no participation_runner "
                    "is composed — [participation] enabled = true requires the "
                    "composition root to build one"
                )
            runner = self._participation_runner
        try:
            # `AgentRunner.invoke` also hands back a `TagTrace` (Task 4), but
            # this path builds no debrief record of its own — §7.4/Task 6
            # wire the tag path's trace into `SqlTagTraceStore`, and this
            # module still has none of that plumbing. Discarded rather than
            # threaded further for now; a fire turn is a real invocation too
            # and picking it up is a flagged follow-up, not a silent gap.
            stop_reason, final_text, _trace = await asyncio.wait_for(
                runner.invoke(
                    doctrine=doctrine, tools=tools, pre_fill=pre_fill, guard=guard,
                    tool_call_log=tool_call_log,
                    tag_message=tag_message, limits=self._limits, ctx=ctx,
                    trace_attributes=trace_attributes(ctx, doctrine),
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            log.error("fire turn invocation_timeout (tag=%s)", ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="invocation_timeout",
                platform=platform, audience=audience,
            )
            failure = FAILED_TIMEOUT
        except Exception:
            log.exception("fire turn llm_error (tag=%s); staying silent", ctx.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="llm_error",
                platform=platform, audience=audience,
            )
            failure = FAILED_ERROR
        finally:
            # `audience` on both, and it is not symmetry for its own sake: this
            # turn was bounded by `[heartbeat] timeout_seconds`, not the tag
            # tier's, so an unlabelled sample blends two populations with two
            # different ceilings and describes neither (§13). Same for the stop
            # reason — the `limit_.*` alarm
            # (`2026-08-08-turn-budget-finding.md:224`) has to say which path
            # ran out of budget, because the caps differ.
            self._metrics.observe(
                "nora_llm_latency_seconds", time.perf_counter() - started,
                audience=audience,
            )
        if stop_reason is not None:
            self._metrics.inc(
                "nora_llm_stop_reason_total", reason=stop_reason, audience=audience,
            )
        # DEC-93, before the silent-failure accounting: recover the prose she
        # wrote without routing it through the tool. Same narrow conditions as
        # the tag path — `end_turn` only, nothing delivered, non-empty — and
        # through `deliver`, so every guard still applies. NEVER on the
        # participation occasion: there the prose was written under
        # `tool_choice="auto"`, so ending in text IS the model declining the
        # floor — "no grounded value is silence", and recovering the draft
        # would deliver exactly the turn the determination declined.
        if (
            not participation
            and failure is None
            and stop_reason == "end_turn"
            # See the tag path: a cancelled model call ends with the GUARD's
            # string. Worse here — recovering it would set `delivered`, so
            # `_turn` returns SENT, the reminder row is written done and never
            # retried, and a reminder that never fired is recorded as fired.
            and not guard.cancelled_model_call
            and not deliverer.delivered
            and final_text.strip()
            and await deliverer.deliver(final_text, source="recovered")
        ):
            self._metrics.inc("nora_recovered_replies_total", platform=platform)
            log.warning(
                "fire turn recovered a reply written WITHOUT send_reply "
                "(tag=%s, %d chars)", ctx.tag_id, len(final_text),
            )

        # `called`, not `attempted` — the same correction as the tag path's
        # `check_outcome` (external review 2026-08-10). A fire turn whose reply
        # was guard-refused DID call the tool, and reporting it as
        # `no_send_call` double-counts one failure under two names. A
        # participation turn whose send was held by the cooldown is the same
        # shape: `called` is True, and the hold was policy, not malfunction.
        if failure is None and not deliverer.delivered and not deliverer.called:
            if participation and stop_reason not in deliver.BUDGET_STOP_REASONS:
                # THE HEALTHY DEFAULT (design/dreamer-agent-loop/35 §6): the
                # model was offered the floor and declined it. Its own series,
                # gated on a completed invocation and a non-budget stop — a
                # turn R-8's caps cut off before it could decide is an ordinary
                # malfunction and falls through to `nora_silent_failures_total`
                # below, NEVER here, or the budget-cut signal would read as
                # success.
                self._metrics.inc(
                    "nora_participation_silence_total", platform=platform,
                )
            else:
                # The loop finished and she never called `send_reply`, or one of
                # R-8's caps cut it off. Labelled the way `check_outcome` labels it
                # (R-23) — `no_send_call` and `limit_total_tokens` are different
                # failures and used to be the same metric. Every OTHER way of not
                # delivering implies `attempted` and has already counted itself
                # inside `Deliverer`.
                reason = (
                    stop_reason if stop_reason in deliver.BUDGET_STOP_REASONS else "no_send_call"
                )
                log.warning("fire turn %s (tag=%s); nothing was sent", reason, ctx.tag_id)
                self._metrics.inc(
                    "nora_silent_failures_total", reason=reason,
                    platform=platform, audience=audience,
                )
        return failure, stop_reason, final_text

    async def _mirror(
        self, occasion: Event, ctx: TagContext, doctrine: Any, *,
        verdict: str, texts: Sequence[str], held: bool,
        stop_reason: str | None,
    ) -> None:
        """The §10.6 shadow mirror: post one participation determination —
        speak AND silence — to the shadow channel, for operator review before
        the flip.

        NOT a third send path: the mirror goes through `deliver.build` and
        the `Deliverer`'s guards (R-2/R-4/R-6/R-18, audit-first) exactly like
        any send, under `SHADOW_AUDIENCE` so its failures never read as tag,
        heartbeat or participation room speech. It is never counted on
        `nora_participation_spoke_total` — that series moves only on the live
        path in `_turn`, and this Deliverer is a separate one. It runs for
        participation occasions only (`_turn`'s two call sites), and only
        when the composition root wired a channel (`[participation] shadow =
        true`, which itself sits on top of `enabled` and the room opt-in).

        Fail-soft in both directions an operator cares about: a missing
        adapter or a faulted send is logged and counted
        (`nora_errors_total{stage="participation_shadow"}`), and NEVER changes
        the reviewed turn's outcome — review traffic must not be able to break
        the path it reviews.
        """
        if self._shadow_channel is None:
            return
        try:
            adapter = self._adapters.get(self._shadow_platform)
            if adapter is None:
                raise KeyError(
                    f"no platform adapter is wired for the shadow platform "
                    f"{self._shadow_platform!r} — wired: {sorted(self._adapters)}"
                )
            identity = await adapter.resolve_identity()
            handle = deliver.build(
                store=self._records, adapter=adapter,
                target=ConversationTarget(
                    platform=self._shadow_platform,
                    conversation_id=self._shadow_channel,
                    # The mirror channel is operator-facing; `group` keeps the
                    # audit row's conversation_type honest without a platform
                    # round-trip to classify it.
                    conversation_type="group",
                ),
                doctrine=doctrine, canary=self._canary, tag_id=ctx.tag_id,
                nora_sender_id=identity.primary,
                conversation_id=self._shadow_channel,
                budgets=self._registry.budgets(),
                terminal=self._registry.terminal_tools(), metrics=self._metrics,
                audience=SHADOW_AUDIENCE,
                # Plain operator-facing text: no roster, no mention composer —
                # `None` is `deliver.build`'s documented inert value, and the
                # keyword has no default there precisely so this is a decision.
                composer=None,
                cacheable_tools=self._registry.cacheable_tools(),
                tool_call_log_store=self._tool_call_log_store,
                # The mirror is not subject to the room's participation
                # cooldown — it is how a cooldown-HELD message is reviewed at
                # all, so holding the mirror too would hide exactly that case.
                send_permitted=True,
            )
            await handle.deliverer.deliver(
                _mirror_text(
                    occasion, ctx, verdict=verdict, texts=texts, held=held,
                    stop_reason=stop_reason,
                    # The mirror is wired, so a held text is held by the
                    # shadow gate (true shadow, Miles decision 2026-09-05),
                    # never by the cooldown — the mirror exists to review
                    # cooldown-held messages, so it is never cooldown-gated.
                    shadow_hold=True,
                ),
                source="shadow",
            )
        except Exception:
            log.exception("participation shadow mirror failed (tag=%s)", ctx.tag_id)
            self._metrics.inc(
                "nora_errors_total", stage="participation_shadow",
                platform=occasion.platform,
            )

    async def _feed(
        self, sent_texts: Sequence[str], conversation_id: str, platform: str,
    ) -> None:
        """Nora's own messages, under the Nora aiPeer, into this conversation's
        session — the tag path's `feedback` stage, awaited rather than spawned.

        Awaited because nobody is waiting on a fire turn, so there is no reply
        latency to protect; guarded because a Honcho outage must not turn a
        reminder that reached the group into a `failed_error` row that says it
        did not.
        """
        if not sent_texts:
            return
        try:
            await feedback.run(
                memory=self._memory, ai_peer=self._ai_peer, sent_texts=sent_texts,
                conversation_id=conversation_id, metrics=self._metrics,
            )
        except Exception:
            log.exception("fire turn feedback failed; the reminder was still delivered")
            # `platform`, because the series DECLARES `(stage, platform)` and an
            # omitted label records `platform=""` (`Metrics._full_labels`) — a
            # sample invisible to `nora_errors_total{platform="signal"}`, which
            # is the filter an operator reaches for first. `fire_context` above
            # already gets this right; this site did not.
            self._metrics.inc(
                "nora_errors_total", stage="fire_feedback", platform=platform,
            )


def _mirror_text(
    occasion: Event, ctx: TagContext, *, verdict: str,
    texts: Sequence[str], held: bool, stop_reason: str | None,
    shadow_hold: bool = False,
) -> str:
    """The shadow mirror post (§10.6): the source room, the verdict, the text
    and the window that closed.

    `texts` is the sent messages for a spoke verdict; for a silent one it is
    the held text (the cooldown kept it — what she WOULD have said) or the
    decline draft (the prose she wrote instead of calling `send_reply`, which
    doubles as her reasoning for declining). The window tail is the last few
    transcript rows the determination saw, so the review reads the trigger in
    context rather than as an orphaned quote.

    `shadow_hold` (Miles decision 2026-09-05): TRUE SHADOW — the mirror is
    wired, so a concluded-speak is held by the shadow gate, not the cooldown.
    The wording must say so: an operator reviewing the mirror needs to know
    the room heard nothing because shadow is on, not because the cooldown
    happened to hold it.
    """
    lines = [
        f"[shadow] {occasion.platform}/{occasion.conversation_id}",
    ]
    if verdict == "spoke":
        lines.append("verdict: SPOKE — the room heard:")
    elif held:
        if shadow_hold:
            lines.append(
                "verdict: SILENT — she concluded speak; SHADOW MODE held it "
                "(the room hears nothing until the flip). She would have said:"
            )
        else:
            lines.append(
                "verdict: SILENT — she concluded speak; the send cooldown held "
                "it. She would have said:"
            )
    else:
        lines.append("verdict: SILENT — she declined the floor.")
        if texts:
            lines.append("her draft (never sent; doubles as her reasoning):")
    lines.extend(texts)
    if stop_reason:
        lines.append(f"(stop: {stop_reason})")
    window = list(ctx.transcript)[-5:]
    if window:
        lines.append("window that closed:")
        lines.extend(
            f"- {row.sender_name}: {(row.body or '')[:120]}" for row in window
        )
    return "\n".join(lines)


def _synthetic_message(
    occasion: Event, ref: PersonRef | None,
) -> NormalizedMessage:
    """§6.4's second table. Built in memory only — nothing writes it.

    `body` is `Event.body`: for a reminder the requester's OWN sentence(s),
    which is the premise of the whole feature (a deferred request, put to her at
    the due time AS a request); for a version announcement the operator's own
    release note, put to her the same way. The circumstances deliberately do NOT
    go here — they belong in the header — because a body that is anything other
    than what somebody actually wrote breaks that premise.

    `sender_id` and `timestamp` are blank/0: falsy, which is what makes
    `indicate.begin` return None and §5.5's "no reactions" hold without a flag
    anywhere. `timestamp` is `0` and not `None` because the field is `int`.

    `sender_name` falls back to `Event.speaker` when no `PersonRef` resolved,
    so a turn nobody asked for still names the author of the words it carries.
    """
    return NormalizedMessage(
        platform=occasion.platform,
        conversation_id=occasion.conversation_id,
        conversation_type=occasion.conversation_type,
        sender_id="",
        sender_name=ref.name if ref is not None else occasion.speaker,
        timestamp=0,
        body=occasion.body,
        raw={},
    )


def _stamp(moment: datetime) -> str:
    """A due/set time in the reader's own timezone. Local, not UTC: the rest of
    the prompt states time locally (`_now_line`), and two clocks in one prompt
    is how she answers with the wrong hour."""
    return moment.astimezone().strftime("%-d %b %H:%M")


def _fire_header(
    rows: Sequence[Reminder], tagger: PersonRef | None, now: datetime,
) -> str:
    """The situation paragraph for a fire turn — `build_prefill`'s `header`.

    Every clause of the tag header is false here: nobody called her, and that is
    not why this turn is happening. `_context_header` is parameterised rather
    than branched so the block catalogue, the notation and — the part that
    actually matters — R-13/R-13b escaping stay shared (§6.3).

    The name is escaped because everything else this module puts in a prompt is,
    and `identity.toml` is operator-authored text like any other.

    `tagger` is typed optional only because `Event.header` is one callable
    type for both occasions; a reminders occasion always names a requester, and
    a turn whose requester fell out of `identity.toml` is refused by `_turn`
    before this is ever rendered.
    """
    who = escape(tagger.name if tagger is not None else "")
    when = "".join(
        f"- asked {_stamp(row.created_at)}, due {_stamp(row.due_at)}"
        f"{_repeat_clause(row)}\n" for row in rows
    )
    request = "request" if len(rows) == 1 else "requests"
    recurring = (
        # Stated only when it is true, so an ordinary one-shot turn's prompt is
        # byte-identical to what it was before DEC-85. It has to be stated at
        # all because without it she has no way to know this is the third of
        # fourteen — and the failure is specific, not vague: she announces a
        # standing arrangement as if it were new («記住喇, 我會日日提你»), every
        # single day, which is exactly how a helpful daily reminder becomes a
        # thing people mute.
        "\nSome of these repeat, as marked. A repeat is not a new arrangement — "
        "you already told them you would do this, so bring the thing up rather "
        "than announcing the reminder again.\n"
        if any(row.series is not None for row in rows) else ""
    )
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        f"just called you. Earlier, {who} asked you to bring something back up "
        "at a particular time, and that time has come — that is why this turn "
        "is happening, and it is the only reason.\n"
        f"When the {request} in tag_message below were made, and when each came "
        f"due (in the same order):\n"
        f"{when}{recurring}"
        f"Say it to {who} now, in your own words, as the person who was asked to "
        "remember. Do not read the times back at them unless they asked in those "
        "terms."
    )


def _repeat_clause(row: Reminder) -> str:
    """« (every day, 11 more after this) » — or nothing at all for a one-shot.

    `remaining_occurrences` is the series' COUNT bound, so this over-states when
    an `until` or a cancel will end the series sooner. That is the right way to
    be wrong here: the number is context for her tone, not a promise she is
    asked to repeat, and the header already tells her not to read the times back
    unless they asked in those terms.
    """
    series = row.series
    if series is None:
        return ""
    left = remaining_occurrences(series)
    more = "last one" if left == 0 else f"{left} more after this"
    # The INTERVAL, not just the unit (#109). Reading only `rule.every` renders
    # a fortnightly arrangement as «every week» and a ten-minute one as «every
    # minute» — wrong by the whole multiplier, in the one paragraph whose
    # stated job is stopping her from misdescribing a standing arrangement.
    step = series.rule.interval
    unit = _EVERY[series.rule.every]
    cadence = unit if step == 1 else f"{step} {unit}s"
    return f" (every {cadence}, {more})"
