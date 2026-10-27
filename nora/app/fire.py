"""`FirePath` — one OCCASION to speak unprompted becomes one actual message.

WHAT THIS MODULE IS RESPONSIBLE FOR
-----------------------------------
The TURN. `nora.app.heartbeat` owns *when* a reminder fires, *whether* policy
still allows it and *what the row then says*; this owns everything between the
two — the synthetic `TagContext`, the prompt, the model call, the delivery, and
the outcome string it answers with.

THERE ARE SEVERAL OCCASIONS, AND THE TURN IS THE SAME TURN
----------------------------------------------------------
The occasions are reminders (DEC-70), gatherings (DEC-139), compute results,
relays (DEC-105), proactive acts and participation. Participation is the one
that triggers on a real room going quiet rather than on a stored row, and the
one where silence is a HEALTHY outcome rather than a failure (`SILENT` below).
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
do not, and the refusals are the design:

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
occasion — because at `"tag"` it would carry the silence rules that
are false here, and an audience that resolved to nothing would deliver with no
profile, no constraints, and a `LeakGuard` corpus carrying nothing but the
authored sections.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from nora.agent.guards import ReplyGuard
from nora.agent.lineage import LineagePolicy, profile_lines_for
from nora.agent.outbound import OutboundComposer
from nora.agent.prefill import (
    build_prefill,
    build_tag_message,
)
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.standing_ledger import read_ledger
from nora.agent.tools import turn_occasion
from nora.doctrine.loader import DoctrineStore
from nora.domain import audience as audience_names
from nora.domain.blocks import RowStyle, render_rows
from nora.domain.cognition_remind import INTENTS
from nora.domain.compute import ComputeJob, DocumentReadBack
from nora.domain.computer_context import ComputerView
from nora.domain.culture import (
    CultureRevision,
    character_key,
    nora_character_key,
    unit_culture_key,
)
from nora.domain.decision_log import (
    PARTICIPATION_DECISION_WRITERS,
    PROACTIVE_ACT_DECISION_WRITERS,
    DecisionLogRecorder,
    DecisionRow,
    DecisionWriter,
)
from nora.domain.documents_context import DocumentsView
from nora.domain.events import Event, EventFactory
from nora.domain.external_files import render_file_links
from nora.domain.framing import (
    ComputeBody,
    ComputeHeader,
    LostJobBody,
    LostJobHeader,
    ParticipationHeader,
    ProactiveHeader,
    RelayHeader,
    ReminderHeader,
    RequestBody,
)
from nora.domain.media import MediaBlob
from nora.domain.media_join import MediaJoin
from nora.domain.memo import Memo, MemoStore
from nora.domain.models import (
    ConversationTarget,
    NormalizedMessage,
    PersonRef,
    TagContext,
    TranscriptRow,
)
from nora.domain.notes import Note
from nora.domain.ports import (
    DEFAULT_MEMORY_READ_TIMEOUT_SECONDS,
    CultureReader,
    JobResult,
    JobStatus,
    LinkPreviewSource,
    MemoryStore,
    OutboundAttachment,
    PlatformAdapter,
    RecordStore,
    RosterSource,
    StandingStore,
)
from nora.domain.profile import ProfileView
from nora.domain.prompt import one_line
from nora.domain.quote_window import QuoteWindow
from nora.domain.reminders import Reminder
from nora.domain.self_presence import Presence, platform_name
from nora.domain.standing import (
    DEFAULT_STANDING_STORE_TIMEOUT_SECONDS,
    Standing,
)
from nora.domain.standing_ledger import LedgerEntry, StandingLedger
from nora.domain.subagent import written_title
from nora.domain.tool_call_log import ToolCallLogStore
from nora.domain.turn_deadline import turn_deadline
from nora.domain.turn_record import TurnCapture, capture_turn
from nora.engagement.character_who import fire_character_people
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.context import StandingNotesReader
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.mentions import MentionRenderer
from nora.engagement.self_presence import PresenceReader
from nora.engagement.stages import deliver, feedback
from nora.engagement.stages.invoke import AgentRunner
from nora.ingest.relevance_gate import SCORE_ABSENT
from nora.language.culture_render import read_character, read_culture
from nora.language.lesson_render import ActiveLessons, read_lessons
from nora.language.profile_render import ProfileHeads, authored_identity, read_profile
from nora.obs.determination import (
    WINDOW_BODY_CHARS,
    Determination,
    Verdict,
    log_determination,
    render_mirror,
    window_from,
)
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span, trace_attributes
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger("nora.app.fire")

# The `audience` label every `nora_silent_failures_total` this path raises
# carries — including the five inside `Deliverer`, which is why `deliver.build`
# takes it. A8 (`docs/runbook.md`) filters on `audience="tag"`, so a fire-turn
# failure recorded without this would be charged to a budget whose
# denominator counts only tags.
AUDIENCE = audience_names.HEARTBEAT

# The four outcomes. The heartbeat derives the ROW's terminal state from the
# string (`_state_for`: only `sent` is DONE), so these are not log text —
# spelling one differently records a delivery that never happened.
SENT = "sent"
FAILED_POLICY = "failed_policy"
FAILED_TIMEOUT = "failed_timeout"

# The three `nora_errors_total{stage=...}` labels the participation turn's
# memory reads raise. NAMED CONSTANTS, not literals at the call
# site, for the reason `nora/obs/metric_specs.py` states about label
# vocabularies: an operator filtering on a stage is filtering on a string, and
# a stage spelled two ways is two series neither of which is the truth. They
# are declared in `metric_specs`' own `nora_errors_total` description, and
# `tests/unit/test_metric_specs_stages.py` is what keeps the two from drifting.
#
# TWO and not one, because they are two different failures and an operator
# must not have to guess which half of the context went blind: a speaker's
# history, or the whole batch hitting its wall clock.
STAGE_FIRE_PEOPLE = "fire_people_in_window"
STAGE_FIRE_MEMORY = "fire_memory"

#: How many of the window's newest rows feed the `search_query` of the
#: participation turn's memory reads, and the character ceiling on the result.
#:
#: The tag path passes the INBOUND MESSAGE as its query (`_recall_tagger`)
#: because there is one. A participation occasion has none —
#: `participation_occasion` builds `body=""` on purpose — so the window is the
#: only statement of what this turn is about. The newest rows rather than the
#: whole window: a twelve-row window curated to its own oldest line retrieves
#: conclusions about a subject the room has already moved past, which is worse
#: than retrieving nothing.
_QUERY_ROWS = 6
_QUERY_CHARS = 1000
FAILED_ERROR = "failed_error"

# The participation occasion's two additions to the outcome vocabulary.
#
# `PARTICIPATION_AUDIENCE` labels every series a participation turn moves
# (silent failures, llm latency, stop reasons) so its failures are never
# charged to the heartbeat's budget, and its doctrine is read at this audience
# — the silence doctrine differs: a heartbeat turn is OBLIGED to speak, a
# participation turn is PERMITTED to.
#
# `SILENT` is the outcome the other four kinds cannot produce: the turn ran,
# the model concluded "nothing here is worth saying", and that is SUCCESS —
# the healthy default, counted on `nora_participation_silence_total`, never
# on `nora_silent_failures_total`. A reminder that says nothing is broken; a
# participation turn that says nothing is working.
PARTICIPATION_AUDIENCE = audience_names.PARTICIPATION
SILENT = "silent"

# The proactive occasion's audience (DEC-162): a `kind="proactive"` reminder
# row — written by Nora's own Cognition session, owed to nobody — coming due.
# Its doctrine is read at this audience (`ProactiveHeader`'s may-speak rule
# reaches it; the heartbeat's obliged-to-speak reading does not), its failures
# are labelled apart from the heartbeat's and participation's, and a turn that
# ends without speaking answers `SILENT`, the same healthy default
# participation has and the heartbeat cannot.
PROACTIVE_AUDIENCE = audience_names.PROACTIVE

# WHICH OCCASIONS READ THE SHARED DECISION LOG, AND WITH WHICH WRITERS — one
# statement, because they are one decision (`_read_decisions`). A kind absent
# from this mapping renders no `<loop_decisions>` block at all: the reminder
# and gathering occasions were decided when their row was written and are not
# weighing whether to take a floor, so the read would cost a query and change
# nothing.
#
# Both values are `nora.domain.decision_log`'s own constants and neither is
# spelled out here, so the writers a query asks for and the deciders the
# block's note glosses cannot drift apart — the module's own rule, and the
# reason those constants exist at all.
_DECISION_WRITERS_BY_KIND: dict[str, tuple[DecisionWriter, ...]] = {
    "participation": PARTICIPATION_DECISION_WRITERS,
    "proactive": PROACTIVE_ACT_DECISION_WRITERS,
}

# The shadow mirror's audience label: the mirror IS a send (it goes
# through `deliver.build` and every guard that carries), but it is operator
# review traffic, never room speech — its failures must not pollute the tag,
# heartbeat or participation budgets, and a mirrored "spoke" must never read
# as a live send on `nora_participation_spoke_total` (that counter moves only
# on the live path below; the mirror has its own Deliverer and never touches
# it).
SHADOW_AUDIENCE = audience_names.SHADOW
# The Relevance Gate's review surface. A
# SECOND shadow audience and not a reuse of the one above, because the two
# mirror different things and an operator reading a dashboard must be able to
# tell them apart: `participation-shadow` carries what she WOULD HAVE SAID
# (a turn ran), `relevance-gate-shadow` carries what the gate ADMITTED (no
# turn ran at all). Collapsing them would make "the gate let 40 windows
# through" and "she drafted 40 replies" the same number.
GATE_SHADOW_AUDIENCE = audience_names.GATE_SHADOW

def reminder_occasion(rows: Sequence[Reminder]) -> Event:
    """One conversation's due reminders, as the occasion they are.

    The heartbeat's rows are already grouped by `(platform, conversation_id,
    requester)`, so `rows[0]` names all four for the whole batch.

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
        body=RequestBody(tuple(r.text for r in rows)).render(),
        header=lambda tagger, now: ReminderHeader(tuple(rows), tagger).render(),
        kind="reminder",
    )


def proactive_occasion(rows: Sequence[Reminder]) -> Event:
    """A `kind="proactive"` row — Nora's own earlier decision — coming due
    (DEC-162): the ACT turn of the OODA loop whose DECIDE step wrote this row.

    `person_id=None`, participation's shape: the row's owner is the reserved
    `ai_peer` id, which is nobody in `identity.toml` by construction,
    so this turn runs on nobody's authority and `build_context` accepts every
    consequence of a `None` tagger. `speak` skips the unmapped-person refusal for it
    exactly as it does for a participation turn.

    `body` is the INTENT the Cognition session's `remind` wrote — «raise the dinner plan now
    that people know where they are coming from» — put to her the way a
    reminder puts the requester's own sentence: as what was asked, in the
    asker's words, and the asker is her earlier self. The header carries
    when she decided it and for when, and names the note it is about
    (`payload["note_title"]`, stamped in code by `remind` at decision
    time). The note's CURRENT body is not here: the fire turn renders the
    `<notes>` index like every fire turn and she reads it with `note_read`,
    which is the point of splitting decide from act — research at 08:00 is
    what to raise, research at 14:00 is where everybody is.

    The heartbeat has already screened the row (`ProactiveScreen`): the flag,
    and whether the note moved on the tag path since the row was linked. So a
    row reaching this factory is one still worth a
    turn. She may still decline: `SILENT` is reachable for this kind.
    """
    row = rows[0]
    # The brief's intent. Older rows hold free text here, so only exactly tell/ask
    # counts; a batch that asks anything is an ask, since one answer is then expected.
    intents = {
        r.payload.get("intent") for r in rows if isinstance(r.payload, Mapping)
    } & set(INTENTS)
    return Event(
        platform=row.platform,
        conversation_id=row.conversation_id,
        conversation_type=row.conversation_type,
        person_id=None,
        body=RequestBody(tuple(r.text for r in rows)).render(),
        header=lambda tagger, now: ProactiveHeader(tuple(rows)).render(),
        kind="proactive",
        intent="ask" if "ask" in intents else "tell" if intents else None,
    )


def compute_occasion(
    job: ComputeJob, result: JobResult, readback: DocumentReadBack | None = None,
    *, checkpoint: str = "", kept_files: tuple[str, ...] = (), kept: MediaBlob | None = None,
) -> Event:
    """A finished compute job, as the occasion it is, delivered through
    `FirePath.speak` (DEC-97).

    `body` is the job's own task description PLUS its result summary —
    the backend's own account of what it built (this text
    passes R-18 like any other reply; the artifact bytes themselves do not).
    Nora composes the actual group-facing message from this in her own
    voice; this function only assembles what she is TOLD, not what she says.

    `readback` is what code found when it read back each `document_id` the result named
    (R-100). `None` means nobody read them back, so every id is reported missing: a link the
    turn was not handed would be stripped anyway, and an unverified document is not saved.
    The read-back URLs ride on the `Event` so the fire turn can seed its link carrier.

    `checkpoint` is where a job that stopped had got to, already rendered to plain markdown by
    the one checkpoint renderer; `kept_files` are the files code saw the
    worker keep. Neither is the worker's report, and neither is read from it.

    A result that carries its artifact's bytes (`result.media`, a video clip) is delivered as an
    ATTACHMENT on her message, and its provider link is neither attached nor put in the body:
    the room receives the clip, never a link that expires. `kept` is its custody handle when the
    poller stored it in the library, so the audit row records the clip as Nora-held.
    """
    if readback is None:
        readback = DocumentReadBack(missing=tuple(result.document_ids))
    attachments: tuple[OutboundAttachment, ...] = ()
    if result.status is JobStatus.SUCCEEDED and result.media:
        media_type = result.media_type or "video/mp4"
        name = kept.sha256[:12] if kept is not None else str(job.id)[:12]
        ext = mimetypes.guess_extension(media_type) or ".bin"
        attachments = (OutboundAttachment(
            content=result.media, media_type=media_type, filename=f"nora-{name}{ext}",
        ),)
        result = replace(result, url=None)
    return Event(
        platform=job.platform,
        conversation_id=job.conversation_id,
        conversation_type=job.conversation_type,
        person_id=job.requester_person_id,
        body=ComputeBody(
            written_title(job.payload) or job.task, result, readback, checkpoint, kept_files,
        ).render(),
        header=lambda tagger, now: ComputeHeader(tagger).render(),
        kind="compute",
        document_urls=tuple(d.url for d in readback.found if d.url),
        # A finished job's own result link rides with the message, attached by code, only when
        # its bytes are not attached instead (a video clip that could not be fetched): the body
        # tells her it is there and not to write it out.
        attached_urls=(
            (result.url,) if result.status is JobStatus.SUCCEEDED and result.url else ()
        ),
        attachments=attachments,
        media_blobs=(kept,) if attachments and kept is not None else (),
    )


def relay_occasion(
    *, destination: Any, requester: PersonRef, text: str, from_platform: str,
) -> Event:
    """A message somebody asked to be passed into another room (DEC-105),
    following DEC-99's ruling: a new way for Nora to speak is an occasion on
    this path, never a further send mechanism.

    That ruling is what keeps R-1b's three links intact. A relay that called
    `adapter.send` itself would bypass R-2's latch, R-4, R-6's leak scan,
    R-18's egress scan AND A9's audit-first write in one step — and the last of
    those is the one that bites quietly: the relayed message would appear in
    the destination room with no row recording it, leaving a hole in that
    room's transcript that Nora cannot see.

    `person_id` is the REQUESTER's, so the destination's fire turn runs under
    the authority of somebody `policy.is_allowed` has already cleared for that
    room — and so the policy re-check at fire time applies to them, as it does to a
    reminder's owner.
    """
    return Event(
        platform=destination.platform,
        conversation_id=destination.primary_id,
        conversation_type="group",
        person_id=requester.person_id,
        body=text,
        speaker=requester.name,
        header=lambda tagger, now: RelayHeader(requester, from_platform).render(),
        kind="relay",
    )


def lost_job_occasion(job: ComputeJob) -> Event:
    """A job whose delivery was lost across a restart.

    Boot recovery cannot know whether the container finished: its handle lives
    in an in-process backend's in-memory registry and did not survive
    (`nora.app.compute_poller.recover`). So this must never say the job
    FAILED — that is a claim about a container nobody looked at, and it may
    well have built the thing perfectly.

    What is true, and all that is true, is that she lost track of it. That is
    R-27's `unavailable` — «I couldn't check», not «there is nothing» — and
    saying it is what closes a promise that was otherwise left open forever:
    the requester was told «I'm working on it» and would otherwise hear
    nothing ever again.
    """
    return Event(
        platform=job.platform,
        conversation_id=job.conversation_id,
        conversation_type=job.conversation_type,
        person_id=job.requester_person_id,
        body=LostJobBody(written_title(job.payload) or job.task).render(),
        header=lambda tagger, now: LostJobHeader(tagger).render(),
        kind="compute_lost",
    )


def participation_occasion(
    *, platform: str, conversation_id: str, conversation_type: str = "group",
    relevance_score: float | None = None,
) -> Event:
    """A room that has gone quiet, as the occasion it is.

    The occasion with no stored row and nobody's request behind it —
    `person_id=None`, because the quiescence
    trigger that fires it (`nora.app.participation.ParticipationTrigger`) owes
    the turn to no one person. Every consequence of a `None` tagger that
    `build_context` documents is accepted here as it is there.

    `body` is EMPTY on purpose, and the header says so: nothing was put to
    her. The whole basis for the determination is the injected context — the
    transcript window, standing, notes and facts that `build_context` and
    `build_prefill` assemble — the fast-by-construction rule: if
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
        header=lambda tagger, now: ParticipationHeader(relevance_score).render(),
        kind="participation",
    )


#: The shadow review post's line: `{who}: {body}`, no handle, no clock. An
#: operator surface — the room and the score sit around it on the same line.
_VERDICT_STYLE = RowStyle()

#: The window beside a shadow verdict: `- Grant: …`, clipped.
_MIRROR_STYLE = RowStyle(handle="bullet")


def _mirror_window(d: Determination) -> str:
    """The closed window, rendered HERE rather than in `nora.obs.determination`.

    That module is gated on importing no `nora.*` at all
    (`test_the_module_imports_nothing_from_nora`), so it cannot reach the shared
    renderer, and a private copy of the line format there is what must not
    exist. Its ONE consumer is this file, which can import
    `nora.domain.blocks` freely, so the render moves to the caller and the
    constraint holds without a second implementation. (The COLLAPSE stays in
    that module, in `window_from`, because `WindowRow.body` promises one line to
    everything that reads it — the log record included, which never comes
    through here.)

    **`markup=False`: this is a Discord message, not a prompt.** An operator
    reads these verdicts in a review channel, so `a < b && c` has to arrive as
    `a < b && c` and a link has to stay clickable. The renderer is a PROMPT
    renderer by default, whose escaping would write `&amp;` in front of a
    person and break every URL on the line.

    **`WINDOW_BODY_CHARS` is applied to text `window_from` has already
    collapsed**, so the bound counts characters the reviewer will see. It is
    applied BEFORE the renderer; with `markup=False` that is the same count
    either way (under `markup=True` escaping could expand a clip of 120 `&`
    into 600 characters).
    """
    if not d.window:
        return ""
    return render_rows(
        d.window, style=_MIRROR_STYLE,
        speaker=lambda r: r.sender_name or "",
        body=lambda r: (r.body or "")[:WINDOW_BODY_CHARS],
        markup=False,
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
        defer_tools: bool = False,
        media_join: MediaJoin | None = None,
        quote_window: QuoteWindow | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: frozenset[str] = frozenset(),
        preview_source: LinkPreviewSource | None = None,
        llm_state: Any = None,
        tool_call_log_store: ToolCallLogStore | None = None,
        reasoning_strip_store: Any = None,
        event_registry: Mapping[str, EventFactory] | None = None,
        standing: StandingStore | None = None,
        ledger: StandingLedger | None = None,
        culture: CultureReader | None = None,
        lessons: ActiveLessons | None = None,
        standing_store_timeout_seconds: float = DEFAULT_STANDING_STORE_TIMEOUT_SECONDS,
        # The participation turn's memory reads. All default to the inert
        # shape: `0` renders no block.
        people_in_window_conclusions: int = 0,
        people_in_window_speakers: int = 0,
        memory_read_timeout_seconds: float = DEFAULT_MEMORY_READ_TIMEOUT_SECONDS,
        notes: StandingNotesReader | None = None,
        policy: Policy | None = None,
        participation_runner: AgentRunner | None = None,
        proactive_runner: AgentRunner | None = None,
        shadow_channel: str | None = None,
        shadow_platform: str = "discord",
        gate_shadow_channel: str | None = None,
        gate_shadow_platform: str = "discord",
        decisions: DecisionLogRecorder | None = None,
        presence: PresenceReader | None = None,
        lineage: LineagePolicy | None = None,
        # `<computer>`: the SAME reader the tag
        # path's assembler holds. `None` while `[computer]` is off.
        computer: ComputerContextReader | None = None,
        # `<documents>`: the SAME reader the tag path's assembler
        # holds. `None` while `[documents]` is off.
        documents: DocumentsContextReader | None = None,
        # `<memos>` (DEC-306): the SAME store the tag path's assembler reads,
        # keyed on the unit like `notes`. `None` renders no block.
        memos: MemoStore | None = None,
        # THE SWITCH (45 §5): the Profile accounts' reader, wired only when
        # `[memory.profile] read` is on. Not `None` means a fire turn renders
        # `<profile>` blocks and none of the reads they replace — the peer
        # card, `people_in_window`, the culture/unit/self heads and the
        # character heads — is made at all.
        profile: ProfileHeads | None = None,
        tag_trace_store: Any = None,
    ) -> None:
        # `<standing>` renders on the fire path too. `build_prefill` is shared
        # and parameterised only by `header=`, so SUPPRESSING the block here
        # would be the special case needing justification.
        #
        # It matters most on exactly this path: a fire turn speaks into a room
        # UNPROMPTED, so «where had this conversation got to» is the context she
        # is least likely to have any other way — there is no tagging message to
        # infer it from. `None` when `[standing] enabled = false`, the shipped
        # default, which renders no block at all.
        #
        # This path READS the position and never writes one: `speak` discards
        # the `TagTrace` that `AgentRunner.invoke` hands back, so there is
        # nothing to fold.
        self._standing = standing
        self._ledger_store = ledger
        self._tag_trace_store = tag_trace_store
        # THE CULTURE HEAD, read on EVERY fire occasion (DEC-207) — see
        # `_read_culture`.
        #
        # Read-only by TYPE — a `CultureReader` has `head` and nothing else — so
        # no fire-path turn can write a culture row. That matters more here than
        # anywhere: the tier's whole claim is that the account is written by a
        # background pass reading a whole batch, never by a turn reacting to one
        # window.
        #
        # `None` while `[culture] enabled = false`, the shipped default.
        self._computer = computer
        self._documents = documents
        self._culture = culture
        # `<lessons>` (L5): `None` while `[lessons] render` is off.
        self._lessons = lessons
        # BOUNDED, for the reason the debrief stage's own store calls are:
        # the heartbeat holds this conversation's slot around the whole fire
        # turn (`heartbeat.py` try_acquire -> release_and_dispatch), and
        # `create_sql_engine` sets no `command_timeout`, so an unbounded read
        # here wedges the conversation exactly as the unbounded read in
        # `debrief.run` did.
        self._standing_store_timeout_seconds = standing_store_timeout_seconds
        # The participation turn reads memory. Every one of these
        # is 0/inert by default and gated on `occasion.kind == "participation"`
        # inside `_read_memory`, so no other fire occasion's prompt changes.
        self._people_in_window_conclusions = people_in_window_conclusions
        self._people_in_window_speakers = people_in_window_speakers
        # BOUNDED for `standing_store_timeout_seconds`' reason, and it is its
        # OWN knob rather than a reuse of that one: an operator tuning how long
        # a single-row Postgres read may take must not silently retune how long
        # an HTTP call to Honcho may take. See `HonchoConfig.read_timeout_seconds`.
        self._memory_read_timeout_seconds = memory_read_timeout_seconds
        # The note tier on the fire path. A participation determination
        # grounds on «transcript + standing/notes», so `build_context` passes
        # the notes reader for every occasion kind: one `build_context` shape,
        # no per-occasion special case, and the tier is default-disabled.
        #
        # `policy` is here for exactly one read: the tier is keyed on
        # `Conversation.unit` (DEC-105), so scoping it needs the same
        # `Policy` instance the assembler holds — a fire path with its own
        # idea of a room's unit would render another social unit's notes
        # into this one. `None` keeps the tier inert.
        self._notes = notes
        self._memos = memos
        self._policy = policy
        # THE SWITCH (45 §5): `None` until `[memory.profile] read` is on — see the
        # parameter. The reads it replaces check it and are not made.
        self._profile_reader = profile
        # Lineage: the SAME value the
        # tag path's assembler holds (`nora.app.assembler.lineage_policy`), so
        # a fire turn's derived blocks are filtered for its room exactly as a
        # tag reply's are. `None` is `[lineage] mode = "off"`.
        self._lineage = lineage
        self._persons = persons
        self._records = records
        # The shared quote-window resolver (DEC-195). `None` keeps
        # `build_context`'s plain `context_window` fetch.
        self._quote_window = quote_window
        # The shared media join. A fire turn renders the SAME
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
        # same map: fire-path eligibility is NOT platform-specific, so a
        # reminder set in a Discord channel fires into that channel.
        self._adapters = adapters
        # Kind -> factory, the dispatch point for reminder-derived Events
        # (DEC-139). Defaults to reminders only; the composition root wires the
        # real registry.
        self._event_registry = dict(
            event_registry if event_registry is not None else {"reminder": reminder_occasion}
        )
        self._registry = registry
        self._canary = canary
        self._display_name = display_name
        self._ai_peer = ai_peer
        self._context_window = context_window
        self._limits = limits
        self._defer_tools = defer_tools
        # `[heartbeat] timeout_seconds`, NOT `[llm] timeout_seconds`. A fire
        # turn has nobody waiting on it, so the tag path's 60s — chosen because
        # a human is staring at a group chat — is the wrong bound here.
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        # Optional for the reason it is optional on the pipeline: `Room` reports
        # an absent source as UNAVAILABLE rather than as an empty room, so a
        # harness degrades honestly instead of answering "nobody is here" (R-5).
        # Platform -> roster source: each room is looked up in its own
        # platform's source, since a source does not know another platform's
        # conversation ids (it would come back `available=False` and every
        # room-scoped tool would answer «I could not look»).
        #
        # A MISSING platform here degrades rather than raising, unlike
        # `_adapter_for`: `Room` already documents an absent source as
        # UNAVAILABLE (never as an empty room), so the honest answer is already
        # built, and an offline/eval composition legitimately has no roster at
        # all. Delivering to the wrong platform is catastrophic; failing to
        # read a roster is merely blind, and blind-but-saying-so is allowed.
        self._rosters: Mapping[str, RosterSource] = rosters or {}
        self._profile_self_ids = self_ids
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
        # The durable reasoning-strip record. Optional and inert when `None`, exactly
        # like `tool_call_log_store` above — the participation occasion is one
        # of the two paths the watch labels, and this is where its rows come
        # from.
        self._reasoning_strip_store = reasoning_strip_store
        # The participation occasion's runner:
        # the SAME AgentRunner shape composed with `tool_choice="auto"` —
        # the determination may end in silence, which `required` (the tag
        # runner's pin) makes inexpressible. `None` whenever
        # `[participation] enabled = false`; `_invoke` selects it by occasion
        # kind, and a participation turn arriving with no runner composed is
        # a wiring fault, not a turn to run on the tag runner.
        self._participation_runner = participation_runner
        # The proactive occasion's runner:
        # the SAME `tool_choice="auto"` shape participation needs, for the
        # same reason — the act turn may end in silence, and the tag runner's
        # `required` pin makes that inexpressible. The composition root hands
        # the one auto-runner object to both parameters when both occasions
        # are wired; `None` whenever no proactive adaptor is composed, and a
        # proactive row arriving then is a wiring fault `_invoke` raises on.
        self._proactive_runner = proactive_runner
        # The shadow mirror. `shadow_channel` None means shadow mode is
        # OFF (the shipped default — `[participation] shadow = false`), and
        # `_mirror` returns without doing anything; set, every participation
        # determination — speak AND silence — is mirrored to that channel on
        # `shadow_platform`, through the same `deliver.build`/guards as any
        # send, under `SHADOW_AUDIENCE`. The composition root wires it only
        # when `enabled` AND `shadow` are both on, so a mirrored determination
        # always cleared the room opt-in first.
        self._shadow_channel = shadow_channel
        self._shadow_platform = shadow_platform
        # The gate's own review channel, separate config from
        # the participation mirror above: one deployment may want gate
        # verdicts without turn drafts, and while shadow is on there ARE no
        # turn drafts to mirror.
        self._gate_shadow_channel = gate_shadow_channel
        self._gate_shadow_platform = gate_shadow_platform
        # The shared decision log, written from `_determined` — the ONE method
        # all four determination exits converge on, so the writer cannot come
        # apart from the record it writes. `None` is the dormant default.
        self._decisions = decisions
        # `<presence>` — the SAME reader the tag
        # path's assembler holds, so a participation turn, a reminder and a
        # proactive act turn know where they are the way a tag does. Read per
        # turn; `None` renders the network alone.
        self._presence = presence

    async def build_context(self, occasion: Event) -> TagContext:
        """The fire turn's context, assembled HERE rather than by `ContextAssembler`.

        **This is the trap the whole section exists for.** The synthetic message
        carries a blank `sender_id` on purpose — that is what makes "no
        reactions" hold by construction. But `TranscriptInjection.assemble`
        resolves the tagger as `persons.resolve(platform, msg.sender_id)`, and a
        blank id resolves to `None`. Every one of these then follows, silently,
        with every gate still green:

        * `ctx.tagger is None`, so `peer_card` is `()` — she remembers nothing
          about the person she is answering
        * `Room(tagger=None).scope()` returns UNAVAILABLE, so every room-scoped
          tool goes dark
        * `search_memory` refuses outright
        * the `tagger is None -> refuse` gate fires on EVERY fire turn, for a
          reason that has nothing to do with trust

        So the tagger comes from the OCCASION's `person_id` — for a reminder,
        the id the tick already screened `policy.is_allowed` against — through
        `Persons.by_person`, and never from the message.

        An occasion with `person_id=None` (a proactive or participation turn) therefore has
        `tagger is None` DELIBERATELY, and every consequence above is accepted
        rather than escaped: nobody asked, so there is no peer card to read and
        no person whose room membership scopes a lookup. What makes that safe is
        that `speak` skips that refusal only for such an occasion, and that the
        tools which go dark are exactly the ones such an occasion has no use
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
        if self._quote_window is not None:
            # DEC-195. A fire occasion carries no
            # single triggering message — `participation_occasion`'s
            # `body=""`, `person_id=None`: she owes the turn to no one row —
            # so there is no ONE quote to check the way the tag path has.
            # `recent_for_window` reads the plain window first and, if a row
            # INSIDE it quotes a parent that window does not itself resolve,
            # grows to reach it — the same condition `_parent_of`
            # (`nora.agent.prefill`) would otherwise fail on when this
            # transcript is rendered. Its failure handling is the
            # `stage="fire_context"` one below.
            transcript = await self._quote_window.recent_for_window(
                conversation_id=occasion.conversation_id,
                context_window=self._context_window,
                platform=occasion.platform, stage="fire_context",
            )
        else:
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
            transcript = await self._media_join.enrich(transcript, path="fire")

        card: tuple[str, ...] = ()
        if ref is not None and self._profile_reader is None:
            # (The switch: with `[memory.profile] read` the occasion person's
            # `<profile>` replaces the card and this read is not made.)
            try:
                card = tuple(await self._memory.peer_card(
                    peer_id=ref.honcho_peer_id,
                    conversation_id=occasion.conversation_id,
                ))
            except Exception:
                # Guarded where `TranscriptInjection`'s equivalent is not:
                # delivery is at-most-once, so a raise here writes
                # `failed_error` and the reminder is GONE, while the tag
                # path's equivalent costs somebody a re-tag.
                # `HonchoClient.peer_card` returns `[]` on failure, so nothing
                # normally reaches this.
                # Its OWN stage, not `fire_context`: an operator asking which
                # half of the context went blind must not have to guess.
                log.exception("fire peer_card read failed; continuing without it")
                self._metrics.inc(
                    "nora_errors_total", stage="fire_peer_card", platform=occasion.platform,
                )

        # The unit is resolved ONCE per turn and read by several tiers (notes,
        # memos, the decision log), so the `notes` guard is not the whole
        # condition: a turn that skipped the lookup because the note tier was
        # off would render no decisions block for a reason unrelated to
        # decisions. Skipped entirely when none is wired (the dormant default
        # must not count a gap it does not have).
        unit: str | None = None
        if self._notes is not None or self._decisions is not None or self._memos is not None:
            unit = self._unit_for(occasion)

        # Read AFTER the transcript, because both reads are
        # curated to the window's own text and keyed on the people in it.
        # The switch: with `[memory.profile] read` the persons' `<profile>` blocks
        # replace `<people_in_window>` and the Honcho recall is not made.
        people = (
            () if self._profile_reader is not None
            else await self._read_memory(occasion, transcript)
        )
        # THE SWITCH (45 §5): the turn's Profile accounts, keyed like the
        # culture reads they replace. Empty on the Honcho surface.
        profiles = await self._read_profiles(occasion, ref, transcript)

        # CONCURRENT, for `TranscriptInjection.assemble`'s reason: these are
        # independent reads, and awaited one after another their latencies
        # stack on the front of every fire turn (`<presence>` alone is a
        # health read per network and a live Instagram handle read).
        # Each helper keeps its own fail-soft handling and none raises, which
        # is what makes one `gather` safe: an exception would otherwise cancel
        # its siblings and take the turn down with it.
        # Nested: `asyncio.gather` types at most six awaitables, and a seventh makes every
        # result a union (the tag path's assembler nests its reads for the same reason).
        (
            (standing, culture, notes, decisions, presence, computer),
            documents,
            character,
            unit_culture,
            self_account,
            (lessons, memos, ledger),
        ) = await asyncio.gather(
            asyncio.gather(
                self._read_standing(occasion),
                self._read_culture(occasion),
                self._read_notes(occasion, unit),
                self._read_decisions(occasion, unit),
                self._read_presence(occasion),
                self._read_computer(occasion),
            ),
            self._read_documents(occasion),
            self._read_character(occasion, ref, transcript),
            self._read_unit_culture(occasion),
            self._read_self_account(occasion),
            asyncio.gather(self._read_lessons(occasion), self._read_memos(occasion, unit),
                           self._read_ledger(occasion, ref)),
        )
        return TagContext(
            tag_id=uuid.uuid4().hex,
            message=_synthetic_message(occasion, ref),
            tagger=ref,
            transcript=transcript,
            peer_card=card,
            started_at=time.perf_counter(),
            people_in_window=people,
            profiles=profiles,
            standing=standing, ledger=ledger,
            culture=culture,
            character=character,
            unit_culture=unit_culture,
            self_account=self_account,
            lessons=lessons,
            notes=notes,
            memos=memos,
            decisions=decisions,
            presence=presence,
            computer=computer,
            documents=documents,
            room_members=self._room_members(occasion),
            lineage_room=(
                self._lineage.render_room(
                    platform=occasion.platform,
                    conversation_id=occasion.conversation_id,
                )
                if self._lineage is not None else None
            ),
        )

    def _room_members(self, occasion: Event) -> tuple[PersonRef, ...]:
        """This room's listed persons for `<room_members>` (a pure lookup, no
        read), the same answer the tag path's assembler gives."""
        if self._policy is None:
            return ()
        conv = self._policy.conversation(occasion.platform, occasion.conversation_id)
        return self._persons.room_members(conv.persons) if conv is not None else ()

    async def _read_computer(self, occasion: Event) -> ComputerView | None:
        """`<computer>` for this occasion's room, or `None` (no reader, or a
        room not using the computer). The reader never raises."""
        if self._computer is None:
            return None
        return await self._computer.read(
            platform=occasion.platform, conversation_id=occasion.conversation_id,
        )

    async def _read_documents(self, occasion: Event) -> DocumentsView | None:
        """`<documents>` for this occasion's room, or `None` (no reader, none yet,
        or the read failed). The reader never raises."""
        if self._documents is None:
            return None
        return await self._documents.read(
            platform=occasion.platform, conversation_id=occasion.conversation_id,
            person_id=occasion.person_id,
        )

    async def _read_presence(self, occasion: Event) -> Presence | None:
        """Where she is for this occasion's room, read now, or `None` with no
        reader wired. Never raises: the reader is written not to, and a
        failure here costs `<presence>` its detail, never the turn."""
        if self._presence is None:
            return None
        try:
            return await self._presence.read(
                platform=occasion.platform, conversation_id=occasion.conversation_id,
                direct=occasion.conversation_type == "dm",
            )
        except Exception:
            log.exception("fire presence read failed; continuing with the network alone")
            self._metrics.inc(
                "nora_errors_total", stage="fire_presence", platform=occasion.platform,
            )
            return None

    async def _read_memory(
        self, occasion: Event, transcript: Sequence[TranscriptRow],
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """`<people_in_window>`, for the PARTICIPATION turn only.

        The participation occasion is built `person_id=None`
        (`participation_occasion`), so `ref` is `None`, so `peer_card` is `()`;
        and `tagger_card` is assembled only by
        `nora.engagement.context.TranscriptInjection` — the TAG path. Without
        this read the turn would know the people in the window by display name
        alone.

        **Participation only, and the restriction is the decision** — the
        narrow half of `_read_decisions`' argument. A reminder or a gathering
        firing was decided when its row was written, so neither reads either.
        The proactive act turn reads the decision log, because «the loop
        already took this floor 31 minutes ago» is a fact about the ROOM, but
        it does not read memory: «what do I know about these people» is
        research the Cognition session already did, at Honcho round trips
        rather than one SQL read, and the split is research at decide time and
        the room at act time. Participation is the turn deciding whether to
        speak into a room nobody addressed her in AND holding no earlier pass
        of its own, which is what makes the memory read change the answer here
        and nowhere else.

        **Fail-soft in two separate places, which is two states and not
        one.** The read catches and counts on its OWN stage — an operator
        asking which half of the context went blind must not have to guess
        — and the batch as a whole carries the wall-clock bound, on a second
        stage, because a hung port that never returns is not the same failure
        as one that raises. In both the cost is the BLOCK, never the turn:
        this is background she may draw on and never the ground for a claim,
        so an absent block costs continuity rather than truth.
        """
        if occasion.kind != "participation" or not self._ai_peer:
            return ()
        query = _window_query(transcript)
        refs = self._window_speakers(occasion, transcript)
        try:
            people = await asyncio.wait_for(
                self._recall_people(occasion, refs, query),
                timeout=self._memory_read_timeout_seconds,
            )
        except Exception:
            # Reachable where the two `except`s below are not: they catch a
            # RAISE, and this catches a port that simply never answers while
            # the heartbeat holds this conversation's slot.
            log.exception("fire memory read exceeded its bound; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage=STAGE_FIRE_MEMORY,
                platform=occasion.platform,
            )
            return ()
        return people

    def _window_speakers(
        self, occasion: Event, transcript: Sequence[TranscriptRow],
    ) -> tuple[PersonRef, ...]:
        """The distinct people who SPOKE in this window, most recent first,
        bounded by `[honcho] people_in_window_speakers`.

        **Most recent first, and the bound is what makes the order matter.** A
        window of twelve rows can hold more speakers than the bound admits, and
        the ones worth a read are the ones still talking — a person who spoke
        once at the top of the window and left is exactly who the turn is not
        deciding about.

        Resolved through `Persons`, so an unmapped sender contributes NOTHING
        rather than a row keyed on a raw platform id: `honcho_peer_id` is the
        roster's canonical id and there is no memory to read under a phone
        number. Nora's own rows are skipped: her own account is `<self>`, read
        separately from the culture store.
        """
        if self._people_in_window_speakers <= 0:
            return ()
        found: dict[str, PersonRef] = {}
        for row in reversed(tuple(transcript)):
            if row.is_nora:
                continue
            ref = self._persons.resolve(occasion.platform, row.sender_id)
            if ref is None or ref.person_id in found:
                continue
            found[ref.person_id] = ref
            if len(found) >= self._people_in_window_speakers:
                break
        return tuple(found.values())

    async def _recall_people(
        self, occasion: Event, refs: Sequence[PersonRef], query: str,
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """`<people_in_window>` — her own `(observer=nora, observed=them)` model
        of each speaker, curated to this window.

        **CONCURRENT, and the reason is arithmetic** — `TranscriptInjection`'s
        gather note, one path over. One `representation` is a round trip, so
        sequential reads would stack N of them on the front of a turn that
        already holds the conversation's slot; gathered, the batch costs one
        and the bound in `_read_memory` covers the lot.

        `return_exceptions=True` rather than a bare gather: one speaker's read
        raising must cost THAT speaker's row, not the block and not its three
        siblings. A person whose read failed is simply absent — and the failure
        is counted, once per failed read, so «her memory of the room went
        quiet» is something an operator can see rather than notice.

        NO new disclosure, for `_recall_tagger`'s reason: these people spoke in
        this room, so `about` could already have been asked about any of them
        on this turn. This surfaces nothing a tool call would not.
        """
        if not refs or self._people_in_window_conclusions <= 0:
            return ()
        results = await asyncio.gather(*(
            self._memory.representation(
                peer_id=self._ai_peer,
                target=ref.honcho_peer_id,
                search_query=query,
                max_conclusions=self._people_in_window_conclusions,
                conversation_id=occasion.conversation_id,
            )
            for ref in refs
        ), return_exceptions=True)
        rows: list[tuple[str, tuple[str, ...]]] = []
        for ref, result in zip(refs, results, strict=True):
            if isinstance(result, BaseException):
                log.error(
                    "fire people-in-window read failed for one speaker; "
                    "continuing without them", exc_info=result,
                )
                self._metrics.inc(
                    "nora_errors_total", stage=STAGE_FIRE_PEOPLE,
                    platform=occasion.platform,
                )
                continue
            if not result.reachable:
                self._metrics.inc(
                    "nora_errors_total", stage=STAGE_FIRE_PEOPLE,
                    platform=occasion.platform,
                )
                continue
            if result.conclusions:
                rows.append((ref.name, result.conclusions))
        return tuple(rows)

    async def _read_decisions(
        self, occasion: Event, unit: str | None,
    ) -> tuple[tuple[str, tuple[DecisionRow, ...]], ...]:
        """What her AMBIENT LOOP already did across this UNIT, for the two
        occasions that decide whether to take a floor nobody offered them.
        `(room label, rows)` groups.

        **Unit-scoped, because «this room» would miss the case it exists
        for.** A `kind="proactive"` row fires into the unit's home room and
        nowhere else, which can be a Discord room while the thread is on
        Signal. A participation turn in the Signal room reading «this room,
        last 24 hours» sees nothing and speaks anyway. The room a decision was made in is rendered
        as its policy
        LABEL instead, which is what a reader actually needs from it.

        **Two occasions, and the restriction is the decision.** Both are
        turns deciding whether to speak into a room nobody addressed her in, so
        «she raised this two hours ago in another room» is the fact that changes
        the answer. A reminder or a gathering firing was decided when its row
        was written and is not weighing the floor at all; handing it this block
        would cost a read and change nothing.

        **The proactive act turn reads it too.** The decide pass's index says
        what she MEANT TO RAISE and nothing about what the loop has since DONE:
        two rows about two notes can come due apart, and the second turn would
        raise again what the first already raised (its window carries the first
        message only as an unlabelled line of her own speech). This block is
        what marks it.

        **Each occasion gets its own writer set** (`decision_log`'s constants,
        which carry the reasoning): participation excludes its own verdicts,
        the act turn excludes `proactive_decide` — the deliberation that
        produced this very turn, already in front of her in the header — and
        reads `proactive_act`, which is the sibling-row fact it exists for.
        Neither reads `tag`: a tag is somebody asking, so what it answered
        belongs in the transcript underneath, where it already is.

        **A turn with no unit renders NO block, rather than falling back
        to this room.** No unit is the wiring gap `_unit_for` already
        refuses to guess past, and a narrower read presented under a block that
        says «across this group» would be a claim the rows cannot support.

        Fail-soft with no `except` here — `DecisionLogRecorder.read_unit`
        answers `()` on every failure and counts it. `None` is the dormant
        default.
        """
        # A MAPPING, not a chain of `or`s: the set a turn reads with IS the
        # decision, and a lookup that answers `None` for an unlisted kind makes
        # «this occasion does not read the log» and «this occasion reads it
        # with these writers» one statement instead of two that can disagree.
        writers = _DECISION_WRITERS_BY_KIND.get(occasion.kind)
        if self._decisions is None or writers is None or not unit:
            return ()
        rows = await self._decisions.read_unit(
            platform=occasion.platform, unit=unit,
            # In the QUERY, never a filter over what came back. The bound is a
            # row count, so a post-filter could let one busy writer's rows evict
            # every other decider's, silently, in exactly the busy room.
            #
            # The SAME constant `build_prefill` hands the renderer, so the
            # block's note names exactly the deciders the query asked for.
            writers=writers,
        )
        return tuple((self._room_label(row), (row,)) for row in rows)

    def _room_label(self, row: DecisionRow) -> str:
        """The operator-authored policy label for the room a decision was made
        in, or `""`. NEVER the conversation id (R-18/R-28) — an unlabelled room
        renders with no prefix rather than with an identifier."""
        if self._policy is None:
            return ""
        try:
            convo = self._policy.conversation(row.platform, row.conversation_id)
        except Exception:
            log.exception("policy read failed while labelling a decision row")
            return ""
        return convo.label if convo is not None else ""

    def _unit_for(self, occasion: Event) -> str | None:
        """The unit this occasion's room belongs to, or `None`.

        The unit-keyed tier scopes through the SAME `Policy` read the
        tag path's own note prefill uses (`nora.engagement.context.
        TranscriptInjection._notes`, DEC-105): two readers each carrying
        their own idea of a room's unit disagree the first time a
        deployment runs two social units.

        `None` — a store wired but no `Policy` or no `Conversation` for the
        room — is a WIRING GAP, not an empty tier: guessing a unit would
        render another unit's notes into this room. Counted, because a
        block missing for the life of a deployment with nothing saying so is
        the `nora_peer_card_empty_total` lesson.
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
            # not turn a deliverable reminder into a silent no-send. Counted,
            # because a block missing for the life of a deployment with
            # nothing saying so is the `nora_peer_card_empty_total` lesson.
            log.exception(
                "policy conversation read failed for %s/%s — the note "
                "tier cannot be scoped to a unit; continuing without it",
                occasion.platform, occasion.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="fire_unit",
                platform=occasion.platform,
            )
            return None
        if convo is None:
            log.warning(
                "no policy conversation for %s/%s — the note tier cannot "
                "be scoped to a unit; continuing without it",
                occasion.platform, occasion.conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="fire_unit",
                platform=occasion.platform,
            )
            return None
        return convo.unit

    async def _read_notes(
        self, occasion: Event, unit: str | None,
    ) -> tuple[Note, ...]:
        """The standing note index, `TranscriptInjection._notes` mirrored.

        Fail-SOFT and COUNTED, the same three states: no store is the dormant
        default (not an error, not counted); no unit is the wiring gap
        above (counted there, once); a FAILED read is neither — a thinner
        prompt beats a reminder that does not fire, but a note index that
        exists and could not be fetched reported as «there are none» is the
        `unavailable`-as-`nothing_found` substitution R-27 exists to prevent.
        """
        if self._notes is None or unit is None:
            return ()
        try:
            return tuple(await self._notes.standing(unit=unit))
        except Exception:
            log.exception("fire note-index read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="fire_notes",
                platform=occasion.platform,
            )
            return ()

    async def _read_memos(self, occasion: Event, unit: str | None) -> tuple[Memo, ...]:
        """The unit's kept memos (DEC-306), `_read_notes` mirrored: no store or
        no unit renders none; a failed read is counted (`stage="fire_memos"`)
        and renders none, never «nothing is kept» said with confidence."""
        if self._memos is None or unit is None:
            return ()
        try:
            return tuple(await self._memos.kept(unit=unit))
        except Exception:
            log.exception("fire memo-index read failed; continuing without it")
            self._metrics.inc(
                "nora_errors_total", stage="fire_memos", platform=occasion.platform,
            )
            return ()

    async def _read_culture(self, occasion: Event) -> CultureRevision | None:
        """This room's current account, or `None`.

        **Every fire occasion reads it** (DEC-207): a reminder, a gathering and
        a proactive act turn all read the account of the room they are about
        to speak into. A proactive act turn is keyed on a unit for its
        DECISION, but by the time it reaches this path it has chosen one
        room, and `occasion.conversation_id` is that room.

        **Resolved through the policy to a `primary_id`**, not read with the
        occasion's `conversation_id`. A Dreamer adaptor is handed `scopes()`, so
        that is the key it wrote under; reading with the arriving thread's id
        matches zero rows on any conversation carrying more than one, and
        renders as «this room has no account yet» — a REAL state, so nothing
        anywhere would report the miss. Every live conversation holds exactly
        one id, so the two keys are equal by accident of data, not by
        construction.

        Bounded, fail-soft and counted under `stage="fire_culture"` by
        `read_culture`, the one read every occasion shares.
        """
        if self._profile_reader is not None:
            # The switch (45 §5): the room's `<profile>` replaced this block.
            return None
        if self._culture is None or self._policy is None:
            return None
        convo = self._policy.conversation(
            occasion.platform, occasion.conversation_id)
        if convo is None or not convo.primary_id:
            return None
        found = await read_culture(
            self._culture.head(key=convo.primary_id), stage="fire_culture",
            platform=occasion.platform, metrics=self._metrics,
            timeout_seconds=self._standing_store_timeout_seconds,
        )
        return found

    async def _read_unit_culture(self, occasion: Event) -> CultureRevision | None:
        """What this room's UNIT is like, keyed
        `unit_culture_key(unit)`: `_read_culture`'s bounds. `None` when the
        tier is off or the unit has no account."""
        if self._profile_reader is not None:
            return None  # the switch (45 §5): the unit's `<profile>` replaced it
        if self._culture is None or self._policy is None:
            return None
        convo = self._policy.conversation(occasion.platform, occasion.conversation_id)
        if convo is None or not convo.unit:
            return None
        return await read_culture(
            self._culture.head(key=unit_culture_key(convo.unit)),
            stage="fire_unit_culture", platform=occasion.platform,
            metrics=self._metrics,
            timeout_seconds=self._standing_store_timeout_seconds,
        )

    async def _read_self_account(self, occasion: Event) -> CultureRevision | None:
        """Her own account in this room's UNIT, keyed
        `nora_character_key(unit)`: `_read_unit_culture`'s bounds. `None` when
        the tier is off, the unit has no account or the room may not show it
        (`Policy.shows_self_in`), so no `<self>` block renders."""
        if self._profile_reader is not None:
            return None  # the switch (45 §5): her `<profile of="nora">` replaced it
        if self._culture is None or self._policy is None:
            return None
        convo = self._policy.conversation(occasion.platform, occasion.conversation_id)
        if convo is None or not convo.unit:
            return None
        # R-31: unit-wide, written from location rooms' sends too.
        if not self._policy.shows_self_in(occasion.platform, occasion.conversation_id):
            return None
        return await read_culture(
            self._culture.head(key=nora_character_key(convo.unit)),
            stage="fire_self", platform=occasion.platform,
            metrics=self._metrics,
            timeout_seconds=self._standing_store_timeout_seconds,
        )

    async def _read_lessons(self, occasion: Event) -> tuple[str, ...]:
        """Her active lessons in this room's UNIT:
        `_read_self_account`'s gates and bounds. `()` while `[lessons] render`
        is off, with no unit, or where `<self>` may not show (R-31)."""
        if self._lessons is None or self._policy is None:
            return ()
        convo = self._policy.conversation(occasion.platform, occasion.conversation_id)
        if convo is None or not convo.unit:
            return ()
        if not self._policy.shows_self_in(occasion.platform, occasion.conversation_id):
            return ()
        return await read_lessons(
            self._lessons.bodies(unit=convo.unit),
            stage="fire_lessons", platform=occasion.platform, metrics=self._metrics,
            timeout_seconds=self._standing_store_timeout_seconds,
        )

    async def _read_character(
        self, occasion: Event, ref: PersonRef | None,
        transcript: Sequence[TranscriptRow],
    ) -> tuple[tuple[str, CultureRevision], ...]:
        """The accounts of the listed persons who are IN this fire (the
        window's speakers and the occasion's own person:
        `fire_character_people`), keyed by this room's UNIT (one per
        (unit, person)):
        `_read_culture`'s bounds, one head per person. `()` when the tier is
        off."""
        if self._profile_reader is not None:
            return ()  # the switch (45 §5): their `<profile>` blocks replaced it
        if self._culture is None or self._policy is None:
            return ()
        convo = self._policy.conversation(
            occasion.platform, occasion.conversation_id)
        if convo is None or not convo.unit:
            return ()
        return await read_character(
            self._culture, unit=convo.unit,
            members=fire_character_people(
                self._room_members(occasion), persons=self._persons,
                platform=occasion.platform, person=ref, transcript=transcript,
            ),
            stage="fire_character", platform=occasion.platform,
            metrics=self._metrics,
            timeout_seconds=self._standing_store_timeout_seconds,
        )

    async def _read_profiles(
        self, occasion: Event, ref: PersonRef | None,
        transcript: Sequence[TranscriptRow],
    ) -> tuple[ProfileView, ...]:
        """The fire turn's Profile accounts (45 §5): hers, the room's, the
        unit's, then one per person the occasion is about
        (`fire_character_people`'s set — the window's speakers and the
        occasion's own person, the same set `<character>` rendered).

        `TranscriptInjection._profiles`' shape and rules, with the fire path's
        own timeout: `read_profile` bounds and counts each read
        (`stage="fire_profile_*"`), a failed read drops that account's view
        and is counted (R-27), and a line whose `exclude_persons` names a
        member of this room is dropped before the view is built. `()` with no
        reader wired (the Honcho surface) or a room outside the policy."""
        if self._profile_reader is None or self._policy is None:
            return ()
        convo = self._policy.conversation(
            occasion.platform, occasion.conversation_id)
        if convo is None or not convo.unit:
            return ()
        reader = self._profile_reader
        members = self._room_members(occasion)
        target = (self._lineage or LineagePolicy.from_policy(self._policy)).render_room(
            platform=occasion.platform, conversation_id=occasion.conversation_id,
        )
        room = Room(
            platform=occasion.platform, conversation_id=occasion.conversation_id,
            roster_source=self._rosters.get(occasion.platform), persons=self._persons,
            tagger=ref, self_ids=self._profile_self_ids,
        )

        async def one(
            key: str, *, entity: str, of: str, stage: str,
            authored: tuple[str, ...] = (),
        ) -> ProfileView | None:
            lines = await read_profile(
                reader.account_heads(key=key), stage=stage,
                platform=occasion.platform, metrics=self._metrics,
                timeout_seconds=self._standing_store_timeout_seconds,
            )
            if lines is None:
                return None
            kept = await profile_lines_for(lines, target, room)
            return ProfileView(entity=entity, of=of, lines=kept, authored=authored)

        views: list[ProfileView] = []
        # HERS, gated like `_read_self_account` (R-31).
        if self._policy.shows_self_in(occasion.platform, occasion.conversation_id):
            view = await one(
                nora_character_key(convo.unit),
                entity="nora", of="nora", stage="fire_profile_self",
            )
            if view is not None:
                views.append(view)
        # THE ROOM's, keyed its `primary_id` like `_read_culture`.
        # `of` is the room's `policy.toml` label, its id in the prompt (ADR
        # 0029). `primary_id` is the store key, often a platform conversation
        # id, and never reaches a prompt. A room with no label shows no block.
        if convo.primary_id and convo.label:
            view = await one(
                convo.primary_id,
                entity="room", of=convo.label,
                stage="fire_profile_room",
            )
            if view is not None:
                views.append(view)
        # THE UNIT's, keyed like `_read_unit_culture`.
        view = await one(
            unit_culture_key(convo.unit),
            entity="unit", of=convo.unit, stage="fire_profile_unit",
        )
        if view is not None:
            views.append(view)
        # THEIRS, with their authored identity lines (45 §3).
        for person in fire_character_people(
            members, persons=self._persons,
            platform=occasion.platform, person=ref, transcript=transcript,
        ):
            view = await one(
                character_key(convo.unit, person.person_id),
                entity="person", of=person.person_id, stage="fire_profile_person",
                authored=authored_identity(
                    name=person.name, aliases=person.aliases,
                    platforms=tuple(sorted({
                        platform_name(p)
                        for (p, _), pid in self._persons.index.items()
                        if pid == person.person_id
                    })),
                ),
            )
            if view is not None:
                views.append(view)
        return tuple(views)

    async def _read_ledger(self, occasion: Event, ref: PersonRef | None) -> tuple[LedgerEntry, ...]:
        if self._ledger_store is None or self._lineage is None:
            return ()
        room = Room(platform=occasion.platform, conversation_id=occasion.conversation_id,
                    roster_source=self._rosters.get(occasion.platform), persons=self._persons,
                    tagger=ref, self_ids=self._profile_self_ids)
        target = self._lineage.render_room(platform=occasion.platform,
                                           conversation_id=occasion.conversation_id)
        try:
            return await asyncio.wait_for(read_ledger(self._ledger_store, target, room),
                                          timeout=self._standing_store_timeout_seconds)
        except Exception:
            log.exception("fire ledger context read failed")
            self._metrics.inc(
                "nora_errors_total", stage="standing_ledger", platform=occasion.platform)
            return ()

    async def _read_standing(self, occasion: Event) -> Standing | None:
        """This conversation's position, or `None`.

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
            found = await asyncio.wait_for(
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
        return found

    async def run(self, rows: Sequence[Reminder]) -> str:
        """The heartbeat's `fire` collaborator: due rows in, outcome out.

        Dispatch by KIND, through the reminder-derived registry
        (DEC-139): `rows[0].kind` selects the
        factory that renders the batch's Event (`"reminder"` →
        `reminder_occasion`, `"proactive"` → `proactive_occasion`); an
        UNREGISTERED kind raises inside this call,
        before `speak`, and `HeartbeatLoop._run_turn`'s `_fire` guard
        resolves the claimed rows to FAILED (`failed_error`) immediately —
        never rendered as a malformed sentence, never left FIRING for boot
        `recover()`.

        The registry defaults to `{"reminder": reminder_occasion}`; the
        composition root wires the real registry.

        It is a separate method rather than a lambda in the composition root
        for two reasons. It keeps `HeartbeatLoop._fire` a
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

    async def speak(
        self, occasion: Event, *, send_permitted: bool = True,
        gate_verdict: str | None = None, gate_score: float | None = None,
    ) -> str:
        """One turn, and the outcome its caller records.

        `gate_verdict`/`gate_score` are the Relevance Gate's upstream
        judgement, threaded from the trigger into the PARTICIPATION occasion's
        trace so a turn can be read back in Langfuse as one story: gate said
        `go` → turn answered `sent`/`SILENT`. Only the trigger passes them
        (every other occasion leaves both `None`).

        **It does not call `invoke.run` or `deliver.check_outcome`.**
        Not for metric isolation (that is what `audience` is for) but because
        it needs its own timeout
        (`[heartbeat] timeout_seconds`) and its own outcome mapping: both of
        those functions answer `bool`, and a `bool` cannot distinguish
        `failed_timeout` from `failed_error`, which is precisely the
        distinction the row has to record. It reimplements the one side effect
        `check_outcome` has that is not accounting — the `llm_state.last_success_at`
        touch — because a successful fire turn IS a successful invocation, and
        omitting it would let a healthy process report a stale LLM to `/health`.

        Only `sent` is DONE, and `sent` means the `Deliverer` actually
        delivered. Anything else is a turn the group never heard — a reminder
        that did not arrive. The
        participation occasion (kind="participation") is the exception that
        proves the reading: for it "the group never heard" is the HEALTHY
        default, so its quiet turn answers `SILENT`, not a failure string.

        `send_permitted` is the participation occasion's SEND cooldown
        threaded into `deliver.build`;
        every other occasion leaves it True. The trigger decides and this
        method enforces — the two are separate so that a trigger bug cannot
        both mistime the evaluation and skip the hold.
        """
        ctx = await self.build_context(occasion)
        # Every log line for the rest of this turn — across every stage it calls
        # into — carries the tag id without naming it (nora.obs.logging's
        # contextvar bridge), so a fire turn is greppable exactly like a tag.
        bind_tag_id(ctx.tag_id)
        # TRUE SHADOW: while the mirror is wired, a participation turn NEVER
        # live-sends — `send_permitted` is forced False so the Deliverer holds
        # the message at the last inch and `_turn` mirrors the draft instead.
        # The operator reviews determinations on the shadow channel; the room
        # hears nothing until the flip (shadow = false). This is what makes
        # shadow a review surface rather than a live send with a copy.
        if occasion.kind == "participation" and self._shadow_channel is not None:
            send_permitted = False
        try:
            # THE ROOT SPAN, for EVERY occasion: without it a kind's `_turn`
            # path would produce a trace with NO ROOT (sibling spans, no name,
            # none of the `nora.*` attributes), which is N traces rather than
            # one with gaps, and the instrument catalogue's first rule is one
            # root per entry point. An outcome such as `failed_timeout` is in
            # Postgres, so the trace needs a span to annotate it onto.
            #
            # An unprompted path is where this matters MOST: nobody is waiting
            # on it, so a silent failure has no human to notice it.
            #
            # `generation` kind on every occasion, not just participation: each
            # of them contains a real model call, which is what the Langfuse
            # sampler selects on. `nora.outcome` is annotated on the way out so
            # the string an occasion answers with — `sent`,
            # `failed_timeout`, `failed_policy`, `failed_error`, `SILENT` — is
            # readable in the trace rather than only in the row.
            with span(
                occasion.kind,
                **{TRACE_KIND: "generation",
                   "nora.platform": occasion.platform,
                   "nora.conversation_id": occasion.conversation_id,
                   "nora.occasion": occasion.kind,
                   # Meaningful only for participation, and False elsewhere
                   # rather than absent — an attribute that appears on some
                   # roots and not others cannot be filtered on.
                   "nora.shadow": (
                       occasion.kind == "participation"
                       and self._shadow_channel is not None
                   ),
                   # The gate's verdict when a participation turn runs behind
                   # one (joins the `relevance_gate` root trace to this turn's
                   # root — gate said go → turn said sent reads off one span).
                   # Same absence rule as `nora.shadow`: every occasion root
                   # carries the pair, blank when no gate ran, so the
                   # attribute is filterable across ALL occasions.
                   **(
                       {"nora.relevance_gate.verdict": gate_verdict or "",
                        "nora.relevance_gate.score": (
                           gate_score if gate_score is not None
                           else SCORE_ABSENT
                       )}
                       if occasion.kind == "participation"
                       else {"nora.relevance_gate.verdict": "",
                             "nora.relevance_gate.score": SCORE_ABSENT}
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
        `EngagementPipeline._run_admitted`'s outer guard, and it carries
        `audience` for the same reason. Without it every stage between `resolve_identity` and
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
        # The participation occasion carries its OWN audience: its doctrine is
        # the may-speak rule set, its failures are never charged to the
        # heartbeat's budget, and its quiet turn is a success (`SILENT`), never
        # a `failed_*`.
        participation = occasion.kind == "participation"
        # The proactive occasion shares participation's two
        # properties — its own audience, and silence as a healthy outcome —
        # and none of its others: no shadow mirror, no cooldown, and the
        # heartbeat (not a trigger) is what brought it here.
        proactive = occasion.kind == "proactive"
        audience = (
            PARTICIPATION_AUDIENCE if participation
            else PROACTIVE_AUDIENCE if proactive
            else AUDIENCE
        )
        try:
            tagger = ctx.tagger
            if tagger is None and occasion.person_id is not None:
                # Only a mapped person may hold a reminder, re-checked here
                # for the same reason policy is — identity.toml is read
                # once at startup and this row may be thirty days old.
                # `failed_policy`, not `failed_error`: nothing went wrong, the
                # grant went away.
                #
                # `and occasion.person_id is not None` is the whole difference an
                # occasion held on NOBODY's behalf makes. Without that clause a
                # participation or proactive turn — which names no requester by
                # design — would refuse itself on a rule about a grant it never had, and
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
                content_checks=self._registry.content_checks,
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
                # `allowed_mcp` left at its default (empty) on purpose: a fire
                # turn is an occasion nobody asked for, so it gets no MCP tool,
                # and the `surface()` call below likewise never passes one.
                budgets=self._registry.budgets(),
                terminal=self._registry.terminal_tools(), metrics=self._metrics,
                param_names=self._registry.tool_param_names(),
                audience=audience,
                intent=occasion.intent,
                cacheable_tools=self._registry.cacheable_tools(),
                tool_call_log_store=self._tool_call_log_store,
                reasoning_strip_store=self._reasoning_strip_store,
                # The participation SEND cooldown. True on every other
                # occasion — the heartbeat has no cooldown, and this keyword's
                # whole reason for travelling through `deliver.build` rather
                # than sitting in the trigger alone is that the trigger decides
                # and the `Deliverer` enforces, at the last inch.
                send_permitted=send_permitted,
                # R-97, armed on the participation occasion ALONE. `None` everywhere else is the
                # rule off, and each
                # `None` is a decision rather than an omission: a reminder, a
                # relay and a finished compute job
                # all exist BECAUSE something is owed to somebody, so a turn
                # that looked and found nothing still owes them that sentence
                # — `lost_job_occasion` is the extreme case, a whole occasion
                # whose only content is «I couldn't check». Proactive is the
                # one near-miss: it shares participation's right to silence
                # and nobody asked there either, but its false-speak
                # rate is unmeasured, and arming a rule on an occasion no
                # instrument is watching ships a change with nothing able to
                # report what it did.
                #
                # The set comes from the REGISTRY, so the tools R-97 watches
                # are derived from the same table that declares them; a
                # literal here would be a second copy of it.
                lookups=(
                    self._registry.lookup_tools() if participation else None
                ),
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
                    content_checks=self._registry.content_checks,
                ),
                content_checks=self._registry.content_checks,
            )
            room = Room(
                platform=occasion.platform, conversation_id=occasion.conversation_id,
                roster_source=self._rosters.get(occasion.platform), persons=self._persons,
                # `None` for an occasion nobody asked for, which is what makes
                # `Room.scope()` report UNAVAILABLE there rather than a room —
                # the honest state (R-21), not a degraded one.
                tagger=tagger, self_ids=self_ids,
            )
            # DEC-74, sourced as the tag path sources it
            # (`EngagementPipeline`): the room's own `location` flag. Left at
            # the surface's `False` default, `where_is` would hold every fire
            # turn to the strict gate (the subset AND the unnamed-account
            # refusals DEC-79 waives), refusing a proactive turn in a
            # `location = true` room where a tag in the same room answers. The
            # flag also relaxes `route`, `saved_places` and parking, as on a
            # tag. No policy, an unlisted room, or a shadow-held participation
            # turn stays strict: a held turn's text is posted to the review
            # channel, another room than the one the check reads.
            fire_convo = (
                self._policy.conversation(occasion.platform, occasion.conversation_id)
                if self._policy is not None
                and not (occasion.kind == "participation" and self._shadow_channel is not None)
                else None
            )
            surface = self._registry.surface(
                # `[llm] defer_tools`.
                defer=self._defer_tools,
                # DEC-256, the same derivation as the tag path. A fire turn's
                # message is never spoken (`_synthetic_message` leaves `voice`
                # False), so this is a text turn; derived rather than
                # hardcoded so the two paths cannot drift.
                occasion=turn_occasion(voice=ctx.message.voice),
                store=self._records, conversation_id=occasion.conversation_id,
                conversation_type=occasion.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer, room=room,
                question=render_file_links(ctx.message.body),
                context_window=self._context_window,
                metrics=self._metrics,
                # DEC-146: the same window `build_prefill` renders below. A fire
                # turn reads a real room's real transcript, so `who_said` works
                # here exactly as it does on a tag — and an occasion nobody asked
                # for is precisely where an unchecked attribution would be worst.
                transcript=ctx.transcript,
                # The one thing that tells a tool this turn was not a tag.
                # `remind` refuses on it: a reminder that can set a reminder
                # is an unbounded loop, and the conversation it would fire into
                # is this one.
                fire_turn=True,
                # A relayed message carries another room's words: its turn may not relay them
                # onward (8765_44837).
                relay_turn=occasion.kind == "relay",
                location_trusted=fire_convo.location if fire_convo is not None else False,
                # DEC-89: the same carrier `handle`'s `Deliverer` holds. A fire
                # turn can absolutely recommend a place («你話要我提你訂枱») and
                # is a message into a real group, so it gets the link behaviour
                # for the same reason it gets the `OutboundComposer` above.
                links=handle.links,
            )
            # R-100: the documents code read back for this occasion (a finished job's) were
            # handed to her in the body, so their links are hers to repeat. Seeded AFTER the
            # surface is built, because building the tools arms the carrier; before it, the
            # mint is a no-op and delivery would strip the very links we are delivering.
            for url in occasion.document_urls:
                handle.links.mint_document(url, published=False)
            for url in occasion.attached_urls:
                handle.links.attach(url)
            for attachment in occasion.attachments:
                handle.links.carry_media(attachment, occasion.media_blobs)
            now = self._clock().astimezone()
            pre_fill = build_prefill(
                ctx, display_name=self._display_name,
                context_window=self._context_window,
                now=now, header=occasion.header(tagger, now),
                catalogue=surface.prompt_catalogue,
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
                doctrine=doctrine, tools=surface.tools, limits=surface.limits(self._limits),
                tool_hooks=surface.hooks,
                pre_fill=pre_fill,
                guard=handle.guard, tool_call_log=handle.tool_call_log,
                reasoning_watch=handle.reasoning_watch,
                tag_message=tag_message, ctx=ctx,
                platform=occasion.platform, deliverer=handle.deliverer,
                participation=participation, proactive=proactive,
                audience=audience,
            )
            # `truncation_reason`'s "the turn reached the end of its try block",
            # decided HERE rather than at the end of it. The tag path can leave
            # it to the last line because a failed invocation returns early
            # there; a fire turn that timed out AFTER delivering still answers
            # `sent` (the outcome is `sent` iff the group heard something),
            # so it runs on to the end of this block, and a `completed = True`
            # down there would tell `truncation_reason` the invocation finished
            # when it was abandoned mid-reply — the one case this counter exists
            # for.
            completed = failure is None
            if not handle.deliverer.delivered:
                if failure is not None:
                    return failure
                if participation:
                    # WHAT the declined floor was made of. Two completely
                    # different turns end here — she had nothing to say, or she
                    # wrote the answer as prose and never routed it through
                    # `send_reply` — and `nora_participation_silence_total`
                    # counts them as one. The length goes to INFO because it
                    # separates them without putting group speech in the log;
                    # the prose itself is DEBUG, for a deliberate diagnosis.
                    if final_text.strip():
                        log.info(
                            "participation turn declined the floor holding %d "
                            "chars of unrouted prose (tag=%s)",
                            len(final_text), ctx.tag_id,
                        )
                        log.debug(
                            "unrouted participation prose (tag=%s): %s",
                            ctx.tag_id, final_text,
                        )
                    # SILENT, not FAILED_ERROR: a participation turn that
                    # delivered nothing is the HEALTHY default, whether
                    # the model declined the floor or the send cooldown held
                    # its message — `_invoke` has already filed the turn under
                    # `nora_participation_silence_total`, or as an ordinary
                    # silent failure if a budget cap cut it off.
                    await self._determined(
                        occasion, ctx, doctrine, verdict="silent",
                        texts=handle.deliverer.held_texts or (
                            (final_text,) if final_text.strip() else ()
                        ),
                        held=bool(handle.deliverer.held_texts),
                        stop_reason=stop_reason,
                    )
                    return SILENT
                if proactive:
                    # SILENT, for participation's reason: she was
                    # offered her own earlier intention and declined it —
                    # the note moved, somebody already said it, it would not
                    # help. The heartbeat maps this to DONE with
                    # outcome="silent", so the decision log reads a
                    # restraint as a restraint and not as a failure.
                    # `_invoke` has already counted it on
                    # `nora_proactive_silence_total`, or as a silent failure
                    # if a budget cap cut the turn off before it decided.
                    if final_text.strip():
                        log.info(
                            "proactive turn declined holding %d chars of "
                            "unrouted prose (tag=%s)", len(final_text), ctx.tag_id,
                        )
                    # Proactive has no send cooldown and no mirror, so `held`
                    # is False and `_determined` reaches the log alone — which
                    # is the whole reason this call exists: otherwise the act
                    # turn's restraint would leave only a counter and a row with
                    # `outcome="silent"`, nothing saying what it declined.
                    await self._determined(
                        occasion, ctx, doctrine, verdict="silent",
                        texts=(final_text,) if final_text.strip() else (),
                        held=False, stop_reason=stop_reason,
                    )
                    return SILENT
                return FAILED_ERROR

            # A delivered reply implies a successful invocation — the touch
            # `deliver.check_outcome` performs, reimplemented rather than
            # inherited. Without it a process whose only recent speech was
            # a reminder reports a stale LLM on `GET /health`.
            if self._llm_state is not None:
                self._llm_state.last_success_at = time.time()
            if proactive:
                self._metrics.inc(
                    "nora_proactive_spoke_total", platform=occasion.platform,
                )
                await self._determined(
                    occasion, ctx, doctrine, verdict="spoke",
                    texts=handle.deliverer.sent_texts, held=False,
                    stop_reason=stop_reason,
                )
            if participation:
                self._metrics.inc(
                    "nora_participation_spoke_total", platform=occasion.platform,
                )
                await self._determined(
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
        limits: dict[str, Any],
        tool_hooks: tuple[Any, ...],
        pre_fill: list[dict[str, Any]],
        guard: ReplyGuard,
        tool_call_log: Any = None,
        reasoning_watch: Any = None,
        tag_message: str,
        ctx: TagContext,
        platform: str,
        deliverer: Any,
        participation: bool = False,
        proactive: bool = False,
        audience: str = AUDIENCE,
    ) -> tuple[str | None, str | None, str]:
        """The model call, with the heartbeat's own timeout and its own
        accounting. Returns `(failure, stop_reason, final_text)`: the
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

        `final_text` travels for the shadow mirror: on a participation
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
        if proactive:
            # Same shape, same reason, its own parameter: a
            # proactive act turn must be able to end in silence, and the tag
            # runner's `required` pin would demand a send.
            if self._proactive_runner is None:
                raise RuntimeError(
                    "a proactive occasion arrived but no proactive_runner is "
                    "composed — a home room in policy.toml requires the "
                    "composition root to build one"
                )
            runner = self._proactive_runner
        trace = None
        captured = TurnCapture()
        try:
            # `[heartbeat].timeout_seconds`, published to the turn's tools:
            # the vision describer was built from the `[llm]` loop and would
            # otherwise spend a heartbeat's whole budget on one read.
            with turn_deadline(self._timeout_seconds), capture_turn() as captured:
                stop_reason, final_text, trace = await asyncio.wait_for(
                    runner.invoke(
                        doctrine=doctrine, tools=tools, pre_fill=pre_fill, guard=guard,
                        tool_call_log=tool_call_log, reasoning_watch=reasoning_watch,
                        tool_hooks=tool_hooks,
                        tag_message=tag_message, limits=limits, ctx=ctx,
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
            # different ceilings and describes neither. Same for the stop
            # reason — the `limit_.*` alarm has to say which path ran out of
            # budget, because the caps differ.
            self._metrics.observe(
                "nora_llm_latency_seconds", time.perf_counter() - started,
                audience=audience,
            )
        # AFTER the try/finally and before anything is recovered or delivered,
        # for `nora.engagement.stages.invoke.run`'s reason: a fire turn that
        # timed out or raised still writes what its loop had already done, and
        # awaiting inside a `finally` on a cancelled task re-raises out of the
        # cleanup path. `flush` is idempotent, swallows its own exceptions and
        # no-ops when the loop never went multi-turn.
        #
        # BOUNDED INSIDE `flush`, by the rule `_standing_store_timeout_
        # seconds` states in this class's `__init__`: the heartbeat holds this
        # conversation's slot around the whole fire turn, and an unbounded
        # store await here would wedge it.
        if reasoning_watch is not None:
            await reasoning_watch.flush()
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
            # NEVER on the proactive occasion either, for the same reason:
            # prose under `tool_choice="auto"` IS the decline.
            and not proactive
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

        # `called`, not `attempted` — as in the tag path's
        # `check_outcome`. A fire turn whose reply
        # was guard-refused DID call the tool, and reporting it as
        # `no_send_call` double-counts one failure under two names. A
        # participation turn whose send was held by the cooldown is the same
        # shape: `called` is True, and the hold was policy, not malfunction.
        if failure is None and not deliverer.delivered and not deliverer.called:
            if participation and stop_reason not in deliver.BUDGET_STOP_REASONS:
                # THE HEALTHY DEFAULT: the
                # model was offered the floor and declined it. Its own series,
                # gated on a completed invocation and a non-budget stop — a
                # turn R-8's caps cut off before it could decide is an ordinary
                # malfunction and falls through to `nora_silent_failures_total`
                # below, NEVER here, or the budget-cut signal would read as
                # success.
                self._metrics.inc(
                    "nora_participation_silence_total", platform=platform,
                )
            elif proactive and stop_reason not in deliver.BUDGET_STOP_REASONS:
                # The same healthy default for the proactive act turn: she read
                # the room again and declined her own earlier intention. Its
                # own series, same budget-cut exclusion.
                self._metrics.inc(
                    "nora_proactive_silence_total", platform=platform,
                )
            else:
                # The loop finished and she never called `send_reply`, or one of
                # R-8's caps cut it off. Labelled the way `check_outcome` labels it
                # (R-23) — `no_send_call` and `limit_total_tokens` are different
                # failures. Every OTHER way of not delivering implies
                # `attempted` and has already counted itself inside `Deliverer`.
                reason = (
                    stop_reason if stop_reason in deliver.BUDGET_STOP_REASONS else "no_send_call"
                )
                log.warning("fire turn %s (tag=%s); nothing was sent", reason, ctx.tag_id)
                self._metrics.inc(
                    "nora_silent_failures_total", reason=reason,
                    platform=platform, audience=audience,
                )
        if self._tag_trace_store is not None:
            from nora.domain.tag_trace import TagTrace
            convo = (self._policy.conversation(platform, ctx.message.conversation_id)
                     if self._policy else None)
            outcome = ("timeout" if failure == FAILED_TIMEOUT else "llm_error") if failure else (
                "send_reply" if deliverer.delivered else "silent"
            )
            trace = trace or captured.trace or TagTrace(
                tag_id=ctx.tag_id, conversation_id=ctx.message.conversation_id,
                messages=(), transcript=ctx.transcript, outcome=outcome,
                created_at=int(self._clock().timestamp() * 1000),
            )
            trace = replace(
                trace, outcome=outcome, transcript=ctx.transcript, platform=platform,
                occasion="participation" if participation else "fire",
                unit=convo.unit if convo else None,
                bound_rooms=(platform + ":" + ctx.message.conversation_id,),
                sent_message_ids=deliverer.sent_message_ids,
            )
            try:
                await asyncio.wait_for(
                    self._tag_trace_store.save(trace), self._standing_store_timeout_seconds,
                )
            except Exception:
                log.exception("fire turn trace could not be retained")
        return failure, stop_reason, final_text

    async def _determined(
        self, occasion: Event, ctx: TagContext, doctrine: Any, *,
        verdict: Verdict, texts: Sequence[str], held: bool,
        stop_reason: str | None,
    ) -> None:
        """Record ONE determination — a turn that could have spoken, and what
        it decided — on every sink that is wired for it.

        Two sinks, and the difference between them is the point. The log takes
        every determination, participation's and proactive's alike, needs no
        channel and costs no send; the shadow mirror takes participation's
        only, and only where the operator wired a channel to review them in.
        Without the log, a proactive decision (the act turn declining its own
        earlier intention) would be readable nowhere but as a counter and a
        database row, and a participation decision in a room with no mirror
        nowhere at all.

        Called at four sites in `_turn`: participation and proactive, silent
        and spoke. `held` is only ever true for participation — proactive has
        no send cooldown — which is why `shadow_hold` is conjoined with the
        occasion rather than read off the channel alone: a proactive
        determination is never held by anything, and a field that says
        otherwise would be a fact nothing produced.
        """
        d = Determination(
            occasion=occasion.kind,
            platform=occasion.platform,
            conversation_id=occasion.conversation_id,
            verdict=verdict,
            texts=tuple(texts),
            held=held,
            shadow_hold=(
                occasion.kind == "participation" and self._shadow_channel is not None
            ),
            stop_reason=stop_reason,
            window=window_from(ctx.transcript),
        )
        log_determination(log, d)
        await self._log_decision(occasion, d)
        if occasion.kind == "participation":
            await self._mirror(occasion, ctx, doctrine, d)

    async def _log_decision(self, occasion: Event, d: Determination) -> None:
        """The determination as a DECISION-LOG row — the sink another decider
        can read.

        The log and the mirror are both write-only as far as a prompt is
        concerned: one goes to a file, one goes to a channel an operator reads.
        Neither is reachable from the gate deciding whether to admit the next
        window in this room, or from a proactive decider deciding what to raise
        in it later. That is the gap this sink closes: three decision paths,
        one room, shared state.

        **`disposition`, never her words.** `Determination.texts` is her reply
        or the draft she declined to send; it stays in the DEBUG half of the
        determination log and in the audit rows. What goes in the row is the
        four-way outcome — `spoke` / `held_shadow` / `held_cooldown` /
        `declined` — which is a label this codebase derives rather than a
        model authors. That is what lets the row ride into a cheap external
        gate call without becoming an egress question.

        Only the two occasions that CAN decline the floor are logged. A
        reminder or a gathering firing is not a decision about whether to
        speak — it was decided when the row was written — and putting it here
        would dilute the block with rows no decider can act on.

        `record` never raises and never blocks past its own timeout, so this
        cannot change the turn's outcome, the property `_mirror` also holds.
        """
        if self._decisions is None:
            return
        writer: DecisionWriter | None = (
            "participation" if occasion.kind == "participation"
            else "proactive_act" if occasion.kind == "proactive"
            else None
        )
        if writer is None:
            return
        await self._decisions.record(
            writer=writer, platform=occasion.platform,
            conversation_id=occasion.conversation_id,
            verdict=d.verdict, reason=d.disposition,
        )

    async def _mirror(
        self, occasion: Event, ctx: TagContext, doctrine: Any,
        d: Determination,
    ) -> None:
        """The shadow mirror: post one participation determination —
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
                param_names=self._registry.tool_param_names(),
                audience=SHADOW_AUDIENCE,
                # Plain operator-facing text: no roster, no mention composer —
                # `None` is `deliver.build`'s documented inert value, and the
                # keyword has no default there precisely so this is a decision.
                composer=None,
                cacheable_tools=self._registry.cacheable_tools(),
                tool_call_log_store=self._tool_call_log_store,
                reasoning_strip_store=self._reasoning_strip_store,
                # The mirror is not subject to the room's participation
                # cooldown — it is how a cooldown-HELD message is reviewed at
                # all, so holding the mirror too would hide exactly that case.
                send_permitted=True,
                content_checks=self._registry.content_checks,
            )
            # `d.shadow_hold` is true here by construction: this method returns
            # early without a channel, and `_determined` conjoins the channel
            # with the participation occasion. That is the true-shadow reading:
            # a held text is held by the shadow
            # gate, never by the cooldown, because the mirror exists to review
            # cooldown-held messages and so is never cooldown-gated.
            mirrored = await handle.deliverer.deliver(
                render_mirror(d, window=_mirror_window(d)), source="shadow",
            )
            if not mirrored:
                # A guard REFUSAL returns False rather than raising, so without
                # this check the stage would count zero errors while dropping
                # determinations the operator was waiting to review. False
                # also covers a send that reached the wire and failed, so the
                # line names `last_outcome` rather than asserting a guard.
                log.error(
                    "participation shadow mirror not delivered: %s (tag=%s)",
                    handle.deliverer.last_outcome or "unknown", ctx.tag_id,
                )
                self._metrics.inc(
                    "nora_errors_total", stage="participation_shadow",
                    platform=occasion.platform,
                )
        except Exception:
            log.exception("participation shadow mirror failed (tag=%s)", ctx.tag_id)
            self._metrics.inc(
                "nora_errors_total", stage="participation_shadow",
                platform=occasion.platform,
            )

    async def post_gate_verdict(
        self, *, platform: str, conversation_id: str, score: float | None,
    ) -> None:
        """Post ONE Relevance Gate GO verdict to the review channel. No turn ran and none will — in
        shadow mode Nora is never involved, whatever the score.

        **Why this lives in fire.py and is not its own module.** R-1b pins
        `deliver.build`'s callers to two MODULES (`pipeline.py`, `fire.py`);
        a gate module calling it would be a third and the rule goes red.
        DEC-188 settled that a second call site INSIDE this module is
        fine ("two caller modules" does not imply "two send call sites"), so
        the gate's review post is a sibling of `_mirror`, here.

        **And it is a sibling rather than a parameter on `_mirror`.** That
        one's signature takes `ctx`, `doctrine` and the TURN'S OUTCOME
        (`verdict`, `texts`, `held`, `stop_reason`); it exists to report what
        she would have said. This runs when no turn happened at all, so every
        one of those arguments would be a placeholder — and a function whose
        arguments are mostly placeholders has two jobs.

        `no_go` NEVER reaches here: only GO is delivered. The
        no_go rate stays observable on
        `nora_relevance_gate_total{verdict="no_go"}`, which is what keeps an
        undelivered verdict from being an unmeasured one.

        Fail-soft and counted, `_mirror`'s posture for `_mirror`'s reason:
        review traffic must never break the path it reviews — here that means
        a failed post must not stop the NEXT window being judged.
        """
        if self._gate_shadow_channel is None:
            return
        tag_id = uuid.uuid4().hex
        try:
            adapter = self._adapters.get(self._gate_shadow_platform)
            if adapter is None:
                raise KeyError(
                    f"no platform adapter is wired for the relevance-gate "
                    f"shadow platform {self._gate_shadow_platform!r} — "
                    f"wired: {sorted(self._adapters)}"
                )
            identity = await adapter.resolve_identity()
            handle = deliver.build(
                store=self._records, adapter=adapter,
                target=ConversationTarget(
                    platform=self._gate_shadow_platform,
                    conversation_id=self._gate_shadow_channel,
                    conversation_type="group",
                ),
                doctrine=self._doctrine_store.get(applies_to=PARTICIPATION_AUDIENCE),
                canary=self._canary, tag_id=tag_id,
                nora_sender_id=identity.primary,
                conversation_id=self._gate_shadow_channel,
                budgets=self._registry.budgets(),
                terminal=self._registry.terminal_tools(), metrics=self._metrics,
                param_names=self._registry.tool_param_names(),
                audience=GATE_SHADOW_AUDIENCE,
                composer=None,
                cacheable_tools=self._registry.cacheable_tools(),
                tool_call_log_store=self._tool_call_log_store,
                reasoning_strip_store=self._reasoning_strip_store,
                send_permitted=True,
                content_checks=self._registry.content_checks,
            )
            posted = await handle.deliverer.deliver(
                await self._gate_verdict_text(
                    platform=platform, conversation_id=conversation_id, score=score,
                ),
                source="shadow",
            )
            if not posted:
                # `Deliverer.deliver` RETURNS False on an R-2/R-4/R-6/R-18
                # refusal; it does not raise, so the `except` below never sees
                # one. Unchecked, a review post refused by the egress screen —
                # say, because the quoted message contains a phone number —
                # would vanish while `nora_relevance_gate_total{verdict="go"}`
                # had already counted it, leaving invisible holes in the GO
                # stream the operator tunes on.
                #
                # It also returns False for a send that reached the wire and
                # FAILED there; `last_outcome` separates the two. The counters
                # do too (a refusal moves `nora_silent_failures_total`, a wire
                # failure moves `nora_replies_total{status="failed"}`).
                log.error(
                    "relevance gate shadow post not delivered: %s (%s/%s) — "
                    "the review channel is missing this GO",
                    handle.deliverer.last_outcome or "unknown",
                    platform, conversation_id,
                )
                self._metrics.inc(
                    "nora_errors_total", stage="relevance_gate_shadow",
                    platform=platform,
                )
        except Exception:
            log.exception(
                "relevance gate shadow post failed (%s/%s)", platform, conversation_id,
            )
            self._metrics.inc(
                "nora_errors_total", stage="relevance_gate_shadow", platform=platform,
            )

    async def _gate_verdict_text(
        self, *, platform: str, conversation_id: str, score: float | None,
    ) -> str:
        """The review post: which room, what was said last, the verdict, the
        score — the four fields the operator asked for.

        The room's own words go on the review channel because that is the
        entire point of the surface: a verdict with no message beside it
        cannot be judged right or wrong. It reaches the channel through the
        SAME `Deliverer` and the SAME guards as any send (R-2/R-4/R-6/R-18,
        audit-first), which is what makes this a review surface rather than a
        second egress.

        A transcript read that fails does NOT stop the post — the verdict and
        the room are the load-bearing half, and a post saying «go, 0.8, could
        not read the room» is more use to a reviewer than no post at all.
        """
        label = ""
        if self._policy is not None:
            convo = self._policy.conversation(platform, conversation_id)
            label = (convo.label if convo is not None else "") or ""
        room = f"{label} ({platform}/{conversation_id})" if label else (
            f"{platform}/{conversation_id}"
        )
        last = "(transcript unavailable)"
        try:
            # A few rows, not one: `recent` takes the newest `limit` but
            # returns them oldest-first, and the newest row can have an empty
            # body (an attachment, a reaction). Scanning back from the newest
            # finds the last thing anyone actually SAID, which is what a
            # reviewer needs beside the verdict.
            raw = await self._records.recent(conversation_id=conversation_id, limit=5)
        except Exception:
            log.exception("relevance gate shadow transcript read failed")
        else:
            named = self._persons.name_transcript(tuple(raw), platform=platform)
            for row in reversed(named):
                # The newest row that actually SAID something — an attachment
                # or a reaction has no words for a reviewer to read.
                if not one_line(row.body or ""):
                    continue
                # ONE ROW, ONE LINE, through the shared renderer. This
                # builds ONE line of the review post
                # (`{room} — {last} — go — {score}` below), so a newline in the
                # message OR in a profile name would break the column an
                # operator reads the verdicts off — `render_rows` collapses
                # both by construction rather than by two calls here.
                #
                # `person_name or sender_name or "unknown"` is the canonical
                # order (DEC-80). A raw `sender_id` (ACI or E.164) must never
                # reach a delivered message: R-18 would refuse the whole post
                # at the Deliverer.
                #
                # `markup=False` for `_mirror_window`'s reason: this string is
                # posted to the operator's review channel, where an escaped
                # `&` is noise and an escaped URL is a dead link. The COLLAPSE
                # and the speaker-delimiter strip both still apply — they are
                # what keeps the column one row per line and one name per row.
                last = render_rows(
                    [row], style=_VERDICT_STYLE,
                    speaker=lambda r: (
                        r.person_name or r.sender_name or "unknown"
                    ),
                    body=lambda r: r.body,
                    markup=False,
                )
                break
            else:
                last = "(no message)"
        # An ABSENT score renders as "—", never as 0.00: the operator tunes
        # the threshold on this column, and a fabricated zero reads as a weak
        # admission where the model simply gave no number.
        shown = f"{score:.2f}" if score is not None else "—"
        return f"{room} — {last} — go — {shown}"

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
            # is the filter an operator reaches for first.
            self._metrics.inc(
                "nora_errors_total", stage="fire_feedback", platform=platform,
            )


def _window_query(transcript: Sequence[TranscriptRow]) -> str:
    """The `search_query` for the participation turn's memory reads: the text
    of the window's newest rows, oldest-first, bounded.

    Bodies only — no names, no ages, no `Nora:` labels. This string is a
    RETRIEVAL KEY handed to Honcho, not a prompt: a speaker's display name in
    it would bias every conclusion toward whoever happened to speak last, and
    the ages would be noise in an embedding. It is bounded on both axes because
    it is built from group-authored text, and an unbounded key is a window
    somebody can make arbitrarily long by typing.

    Her OWN rows are excluded for the same reason `_window_speakers` excludes
    them: the question is what the ROOM is talking about, and an answer she
    already gave is the loudest self-echo there is.
    """
    # FILTER, then slice: slicing first would let a window whose newest six
    # rows are four of her own replies yield a two-row retrieval key, which is
    # not «the window's newest rows».
    bodies = [
        render_file_links(row.body).strip() for row in transcript
        if not row.is_nora and row.body and row.body.strip()
    ]
    return "\n".join(bodies[-_QUERY_ROWS:])[:_QUERY_CHARS]


def _synthetic_message(
    occasion: Event, ref: PersonRef | None,
) -> NormalizedMessage:
    """The synthetic message of a fire turn. Built in memory only — nothing writes it.

    `body` is `Event.body`: for a reminder the requester's OWN sentence(s),
    which is the premise of the whole feature (a deferred request, put to her at
    the due time AS a request). The circumstances deliberately do NOT
    go here — they belong in the header — because a body that is anything other
    than what somebody actually wrote breaks that premise.

    `sender_id` and `timestamp` are blank/0: falsy, which is what makes
    `indicate.begin` return None and "no reactions" hold without a flag
    anywhere. `timestamp` is `0` and not `None` because the field is `int`.

    `sender_name` falls back to `Event.speaker` when no `PersonRef` resolved,
    so a turn nobody asked for still names the author of the words it carries.

    **`voice` is left at its `False` default, DELIBERATELY.** Every occasion
    that reaches here — a reminder, a proactive row, a participation turn, the
    destination half of `send_to` — goes out as TEXT, even when the request
    that created it was SPOKEN: a reminder asked for aloud at 14:00 arrives as
    a message in the room at 20:00.

    The routing key is the TURN, so this default is what decides the surface;
    a deferred reply can always be a text message. Speaking unprompted into a
    voice channel is a much larger claim than answering a question that was
    just asked in one.
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
