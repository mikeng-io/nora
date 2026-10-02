"""`IngestPipeline` — runs the ambient stages in order (spec §4.3).

A readable table of contents, not a decision-maker: every branch belongs to
the stage that owns it (`nora.ingest.stages.*`). This module only sequences
the four calls and assembles the result — nothing here inspects message
content, catches an exception, or decides anything.

`run` takes an already-normalized `message`, not a raw event: parsing (the
former stage 1) moved onto the adapter itself
(`nora.platform.signal.adapter.SignalAdapter.listen`), which drops anything
that parses to `None` — a DM, a sync message, a self-send, an empty body —
before this pipeline ever sees it. `PlatformAdapter` deliberately has no
`parse()` (`nora.domain.ports`'s module docstring), so `IngestPipeline` is not
the place that could do this parsing generically even if it wanted to. See
Trap B in `docs/superpowers/plans/2026-08-05-nora-phase4-platforms.md`.

`message=None` in the returned `IngestResult` means either that the
conversation is not allowlisted (the scope stage refuses BEFORE the audit, so
a denied room is never recorded — see that module for why the room and the
sender are enforced in different places) or that the audit write failed (the
audit stage drops rather than re-raises). A message
that made it past that is always paired with a `decision`, even for an
unmapped sender (D6) or a policy error (the gate stage degrades closed rather
than propagating).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, get_args

from nora.domain.delete_authorship import DELETER_IDS_KEY
from nora.domain.models import (
    EditedMessage,
    EditTarget,
    NormalizedMessage,
    OrphanDelete,
    Touch,
)
from nora.domain.ports import (
    BlobStore,
    DeleteWithdrawal,
    DeleteWithdrawer,
    EngageDecision,
    EngagePolicy,
    MediaObjectStore,
    MemoryStore,
    RecordStore,
    Transcriber,
)
from nora.ingest.comprehension import MediaComprehension
from nora.ingest.stickers import StickerReader
from nora.obs.tracing import TRACE_KIND, annotate, span
from nora.persons import Persons
from nora.platform.registry import MediaSourceRegistry
from nora.policy import Policy

from .stages import audit as audit_stage
from .stages import edit_gate as edit_gate_stage
from .stages import edit_media as edit_media_stage
from .stages import edit_record as edit_record_stage
from .stages import edit_target as edit_target_stage
from .stages import gate as gate_stage
from .stages import identity as identity_stage
from .stages import media as media_stage
from .stages import memory_feed as memory_feed_stage
from .stages import quote_target as quote_target_stage
from .stages import scope as scope_stage
from .stages import stimulus as stimulus_stage
from .stages import touch as touch_stage

log = logging.getLogger("nora.ingest.pipeline")

#: EVERY way an ingest can end, as a CLOSED set. It is a `Literal` rather than a
#: loose string so the vocabulary is DERIVED from the code — the test that says
#: "every exit stamps an outcome" reads `get_args` off this and never
#: hand-copies a list that could drift from the call sites. It is also the label
#: vocabulary of `nora_ingest_outcomes_total`, which is where an operator now
#: READS these (R-76): the ingest trace is plumbing and is not exported, so the
#: counter is the live instrument and the span attribute is latent.
#:
#: Five names over three `return` statements: the two refusals stop early, and
#: the third return covers the three dispositions a message that got all the way
#: through can have. A SIXTH ending is not in here on purpose — an exception —
#: because `nora.obs.tracing.span` already stamps `nora.outcome` with the
#: exception's type name and sets the span status to ERROR, and a name this
#: module also owned would let a crash be reported as one of these.
IngestOutcome = Literal[
    "scope_refused",        # the room is not allowlisted; nothing was recorded
    "audit_refused",        # the audit write failed; the message was dropped
    "sender_not_allowed",   # recorded, and deliberately not answered
    "engaged",              # recorded, and handed to the engagement path
    "recorded",             # recorded, and that is all — the ambient majority
    # The EDIT path (design/message-edits/02 §4). An edit is not a message, so
    # it gets its own endings rather than borrowing `recorded`/`engaged`: a
    # rate of edits is a different question from a rate of messages.
    "edit_recorded",        # folded onto the message it edits
    "edit_engaged",         # folded, and it newly addresses her — a turn
    "edit_unchanged",       # text and mentions unchanged; no turn, no feed
    "edit_stale",           # older than the edit on record (a redelivery); nothing written
    "edit_orphaned",        # no original on record; dropped, whoever sent it
    "edit_refused",         # the original is hers, or its author is not the editor
    # REACTIONS and DELETES (design/cognition-loop/20-observe.md §1, step 1):
    # Record items attached to the message they touch, never messages, so
    # they get their own endings for the edit path's reason.
    "reaction_recorded",    # recorded beside the message it reacts to
    "reaction_orphaned",    # no message on record to attach it to; dropped
    "delete_recorded",      # recorded beside the message it deletes
    "delete_orphaned",      # no message on record to attach it to; dropped
    "delete_refused",       # an author-only delete naming someone else's message
    # A message RECORDED and then stopped before any downstream stage (the central
    # guard, `_guard`): `withdrawn` when a delete hid it (or was applied to it from a
    # held delete); `withdrawal_pending` when a held delete is KNOWN to name it and
    # could not be applied (the start-up sweep applies it); `guard_read_failed` when the
    # question «is a delete holding this message?» could not be answered twice running.
    # That last one is a DELIBERATE stop, counted: no delete is known to exist, the
    # database is failing, and a message fed on a guess is the worse way to be wrong.
    "withdrawn",
    "withdrawal_pending",
    "guard_read_failed",
]

_OUTCOMES: frozenset[str] = frozenset(get_args(IngestOutcome))


def _outcome(metrics: Any, message: NormalizedMessage, value: IngestOutcome) -> None:
    """Record what this ingest actually did — ON A COUNTER, and on the root span
    as well.

    THE COUNTER IS THE LIVE ONE (R-76). An ingest contains no model call, so its
    trace is plumbing and `nora.obs.tracing`'s sampler drops it before it
    reaches the collector; «how many messages ended each way, on which platform,
    in which kind of room» is a Prometheus question and this is where it is
    answered. `platform` and `conversation_type` ride along because they are the
    two dimensions that make the series readable — a room type is what separates
    a group Nora watches from a DM, and a stuck platform is invisible in a
    total.

    THE SPAN ATTRIBUTE IS LATENT, and kept on purpose. The ingest trace is not
    deleted, only unexported: it is opened, populated and dropped at the
    sampler. That is what makes «ingest reaches the collector with nothing» a
    measurement of the RULE rather than of an absence — a pipeline that opened
    no spans would satisfy it for the wrong reason — and it is what makes the
    reversal condition one declaration rather than a re-instrumentation.

    **Never load-bearing.** A tracer that cannot record must not stop an ingest,
    which is why the span half goes through `annotate` (guarded there, once) —
    and the counter half is guarded here for the same reason, because an ingest
    runs on the listener's await path and an exception from an observation would
    stop ingest for every conversation on the adapter.

    The vocabulary check LOGS rather than raising or withholding: an unknown
    outcome is still recorded, because refusing to record an unexpected value
    would leave the case least understood with no record at all. `make gate`'s
    mypy does not reach `nora/ingest`, so the `Literal` annotation alone is a
    claim rather than a check — hence the runtime membership test.
    """
    if value not in _OUTCOMES:
        log.warning(
            "ingest outcome %r is not in the declared vocabulary %s — recording it "
            "anyway; add it to IngestOutcome",
            value, sorted(_OUTCOMES),
        )
    try:
        metrics.inc(
            "nora_ingest_outcomes_total",
            platform=message.platform,
            conversation_type=message.conversation_type,
            outcome=value,
        )
    except Exception:  # an observation must never stop an ingest
        log.debug("ingest outcome %r not counted", value, exc_info=True)
    annotate(**{"nora.outcome": value})


def _held_outcome(held: str) -> IngestOutcome:
    """The ending of a message a held delete stopped: see `IngestOutcome`."""
    if held == "applied":
        return "withdrawn"
    return "guard_read_failed" if held == "unreadable" else "withdrawal_pending"


@dataclass(frozen=True)
class IngestResult:
    message: NormalizedMessage | None
    decision: EngageDecision | None


class IngestPipeline:
    def __init__(
        self,
        *,
        audit: RecordStore,
        memory: MemoryStore,
        persons: Persons,
        policy: EngagePolicy,
        allowlist: Policy,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], None],
        media_sources: MediaSourceRegistry | None = None,
        blobs: BlobStore | None = None,
        media_store: MediaObjectStore | None = None,
        stickers: StickerReader | None = None,
        transcriber: Transcriber | None = None,
        comprehension: MediaComprehension | None = None,
        language: str = "",
        stimulus_wiring: stimulus_stage.Wiring | None = None,
        delete_withdrawer: DeleteWithdrawer | None = None,
        withdrawal_retry_delays: tuple[float, ...] = (2.0, 15.0, 120.0),
    ) -> None:
        """`media_sources`/`blobs` default to None — media custody OFF — and
        that default is a wiring state, not a design position (DEC-43).

        Both are optional so this constructor stayed compatible with a
        composition root being edited in another worktree on the same day.
        With either absent, `media` counts every attachment it sees as
        `not_configured` and records the message exactly as before; nothing is
        silently skipped. Supplying both is what turns custody on — the
        registry is what then picks the RIGHT fetcher per message, by
        platform — and it is the one line the composition root owes this
        feature.

        `stickers` is `sticker_descriptions`'s wiring state, and it is a
        SEPARATE switch from custody: `None` is the deployment default and
        behaves exactly like custody-without-interpretation (a handle, no
        description), never like a failure. Only stickers are ever read this
        way — see `nora/ingest/stages/media.py`'s docstring for why.

        `comprehension` is `[media] comprehension`'s wiring state and the
        SPAWNED read (issue #116): `None` — the inert default, and every
        offline/eval composition — leaves every attachment with a bare handle
        and nothing MODEL-READ. It does NOT silence the voice-note lane: the
        custody stage writes an STT transcript to `media_objects.content`
        itself, inline, because those words were captured synchronously and
        holding them behind a job that may not exist discarded them outright.
        Present, each held blob additionally gets one model pass whose three
        fields land in `media_objects` and in Honcho, off this path.

        `transcriber` is `[speech.stt]`'s wiring state, the same shape as
        `stickers`: `None` (deployment default) records a voice note with a
        bare handle, exactly as before speech existed. When supplied, voice
        notes are transcribed inline during custody and their transcript rides
        the body beside the handle. `language` is the BCP-47 hint forwarded to
        the transcriber.

        `stimulus_wiring` is the stimulus stage (design/cognition-loop/20 §3):
        `None` — the default, `[cognition] enabled = false`, and every offline/
        eval composition — writes no stimulus. Present, every recorded
        person-authored item (a message, a changed edit, a reaction, a delete)
        appends one stimulus row with its cues.

        `delete_withdrawer` is what honours a delete or unsend
        (`nora.app.delete_withdrawal`): present, a delete of a recorded message
        hides it and marks what cites it `unsupported`, and a delete whose target is
        not on record is kept durably (`orphan_delete`) and applied when the message
        is audited. `None` — an offline or eval composition — records the delete and
        withdraws nothing, as before.
        `withdrawal_retry_delays` is the bounded retry after a withdrawal that
        failed (`cascade_failed`): one attempt per delay, then the start-up sweep
        (`nora.app.delete_withdrawal.reconcile_deletes`) is what finishes it.
        """
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._policy = policy
        self._allowlist = allowlist
        self._metrics = metrics
        self._spawn = spawn
        self._media_sources = media_sources
        self._blobs = blobs
        self._media_store = media_store
        self._stickers = stickers
        self._transcriber = transcriber
        self._comprehension = comprehension
        self._language = language
        self._stimulus_wiring = stimulus_wiring
        self._delete_withdrawer = delete_withdrawer
        self._withdrawal_retry_delays = withdrawal_retry_delays

    async def run(self, *, message: NormalizedMessage) -> IngestResult:
        """`message` is already normalized — the caller (the adapter's `listen()`
        loop, via `nora.app.dispatch.Dispatcher.on_message`) never hands this a
        `None`. The `nora_messages_total` increment replaces what the deleted
        parse stage used to do right after a successful `adapter.parse()`."""
        # THE ROOT, and what it carries is the point (issue #64's ingest half).
        # It used to carry a platform and a room key and nothing else, so an
        # ambient message — every ambient message — rendered in Langfuse as a
        # trace with six named children and no statement of what the ingest DID.
        # These four are what tell one ingest from another WITHOUT expanding a
        # child, and `nora.outcome` joins them on the way out.
        #
        # R-73 — A TRACE IS AN EGRESS. Everything here is a room-scoped or
        # structural fact: no sender id, no display name, no message body, no
        # coordinate. `conversation_id` is the one borderline value and it is the
        # repo's own definition that settles it (`scan_identifiers` returns None
        # for both the Signal base64 and the Discord snowflake form); the test
        # section asserts that of every attribute on every span this opens,
        # rather than leaving it to review.
        #
        # `nora.attachments` is what the PLATFORM said arrived, read before
        # custody runs — so a slow `ingest.media` child explains itself, and a
        # trace where custody dropped everything is distinguishable from one that
        # had nothing to fetch.
        #
        # AND IT DECLARES ITSELF PLUMBING (R-76), which is what stops all six of
        # these spans reaching Langfuse. There is no model call anywhere under
        # this root — six deterministic stages, a row, a feed — and Langfuse is
        # for traces that contain a generation. Ingest is the highest-volume
        # entry point in the system, so this one attribute is the difference
        # between a collector an operator can read and one that is 97% messages
        # Nora was never going to answer (measured, 2026-08-28). What the trace
        # carried is not lost: it moved to `nora_ingest_outcomes_total`, which
        # `_outcome` writes on every exit. The declaration must be passed HERE,
        # at span start, because a sampler cannot see an attribute set later.
        with span(
            "ingest",
            **{TRACE_KIND: "plumbing",
               "nora.platform": message.platform,
               "nora.conversation_id": message.conversation_id,
               "nora.conversation_type": message.conversation_type,
               "nora.attachments": len(message.media)},
        ):
            return await self._run(message=message)

    async def _run(self, *, message: NormalizedMessage) -> IngestResult:
        """The body, so the root span in `run` wraps EVERY exit including the
        early returns. Splitting it is what makes `nora.outcome` reachable from
        each `return` without a `try/finally` that would have to re-derive which
        exit it was.

        Every `_outcome` and `annotate` below sits OUTSIDE the stage's own `with
        span(...)`, so the span they land on is the root this method runs inside
        — the one the operator reads first. A `finally` here would be the wrong
        shape twice over: it could not name which of the three returns it was
        without re-deriving it, and it would also swallow the exception ending
        that `span` already records for itself."""
        m = self._metrics
        # Messages and edits, as it always counted. A reaction or a delete is
        # not a message (design/cognition-loop/20 §1): it is counted on
        # `nora_ingest_outcomes_total` (`reaction_*`, `delete_*`), never here,
        # so this series keeps the meaning its dashboards were built on.
        if message.touch is None:
            m.inc("nora_messages_total", platform=message.platform)

        # The ROOM gate, before anything is written: a conversation the
        # operator has not allowlisted is not recorded at all.
        with span("ingest.scope"):
            admitted = await scope_stage.run(
                message=message, policy=self._allowlist, metrics=m
            )
        if not admitted:
            # Ends here, and ENDS VISIBLY: without the attribute an unallowlisted
            # room is a trace that simply stops, indistinguishable from a crash.
            _outcome(m, message, "scope_refused")
            return IngestResult(message=None, decision=None)

        # AN EDIT takes its own path from here (design/message-edits/02 §4):
        # it is recorded beside the message it changes, never as a message of
        # its own — and an edit of nothing on record is dropped, not ingested.
        if message.edit_of is not None:
            with span("ingest.edit_target"):
                found = await edit_target_stage.run(
                    message=message, target=message.edit_of, audit=self._audit,
                    persons=self._persons, metrics=m,
                )
            if found.ending is not None or found.original is None:
                _outcome(m, message, found.ending or "edit_orphaned")
                return IngestResult(message=None, decision=None)
            return await self._run_edit(
                message=found.message, original=found.original,
            )

        # A REACTION or a DELETE (design/cognition-loop/20-observe.md §1):
        # recorded beside the message it touches, never as a message, and
        # never a turn — a reaction is not an utterance, a delete says nothing.
        if message.touch is not None:
            return await self._run_touch(message=message, touch=message.touch)

        # Identity first, so the resolved person_id lands in the audit INSERT
        # instead of a second write. `Persons.resolve` is two dict lookups on a
        # startup-built frozen mapping — no I/O, so nothing fallible moved
        # ahead of the record.
        with span("ingest.identity"):
            person = await identity_stage.run(
                message=message, persons=self._persons, metrics=m
            )
        # A BOOL, never the person_id (R-73). Whether the sender is somebody
        # `identity.toml` knows is the fact that explains the rest of the trace —
        # an unmapped sender is never fed to Honcho (D6) and is refused by an
        # explicit `persons` list — and it is the one thing `nora_unmapped_
        # senders_total` cannot tell you about THIS message.
        annotate(**{"nora.person_known": person is not None})
        # Custody BEFORE the record, not after it (A9, DEC-43). This is the one
        # stage that returns a new message: the body gains the durable handle
        # for every attachment whose bytes are now Nora's, so the row and the
        # blob it names are committed by the same INSERT. See that module for
        # why it is inline rather than spawned like `memory_feed`.
        with span("ingest.media"):
            message = await media_stage.run(
                message=message, sources=self._media_sources, blobs=self._blobs,
                media_store=self._media_store, metrics=m,
                stickers=self._stickers, transcriber=self._transcriber,
                comprehension=self._comprehension,
                withdrawn=self._withdrawn_gate(message, person),
                # `person` is resolved above and rides in ONLY so a spawned
                # comprehension can be fed to Honcho under the sender's own
                # peer (D6, mapped senders only). The custody stage neither
                # resolves identity nor reads it for anything else.
                person=person, language=self._language,
            )
        # A reply to an EDITED message quotes the revision's timestamp on
        # Signal; point it at the row the transcript holds before it is
        # written, so every reader resolves the parent as it always has.
        with span("ingest.quote_target"):
            message = await quote_target_stage.run(
                message=message, audit=self._audit, metrics=m,
            )
        with span("ingest.audit"):
            row_id = await audit_stage.run(
                message=message, audit=self._audit, metrics=m, person=person
            )
        if row_id is None:
            _outcome(m, message, "audit_refused")
            return IngestResult(message=None, decision=None)
        # A DELETE THAT OVERTOOK THIS MESSAGE (a photo unsent while its custody
        # fetch ran, or a delete that came before a restart): applied now, before
        # anything reads it or feeds it on. Then THE ONE GUARD: whatever raced, the row
        # is read again and a hidden one stops here (see `_guard`).
        if self._delete_withdrawer is not None:
            held = await self._apply_orphans(
                message, person.person_id if person else None, row_id,
            )
            if held != "none":
                _outcome(m, message, _held_outcome(held))
                return IngestResult(message=None, decision=None)
        stopped = await self._guard(row_id, message)
        if stopped is not None:
            _outcome(m, message, stopped)
            return IngestResult(message=None, decision=None)
        # The STIMULUS, right behind the Record row it names (20 §3). Inside the
        # guard: a span for a stage that cannot run would claim work that never
        # happened.
        if self._stimulus_wiring is not None:
            with span("ingest.stimulus"):
                await stimulus_stage.run(
                    message=message, item="message", row_id=row_id,
                    person_id=person.person_id if person else None,
                    wiring=self._stimulus_wiring, metrics=m,
                )
        with span("ingest.memory_feed"):
            await memory_feed_stage.run(
                message=message, person=person, memory=self._memory, metrics=m,
                spawn=self._spawn,
            )

        with span("ingest.gate"):
            decision = await gate_stage.run(
                message=message, policy=self._policy, metrics=m
            )
        # The GATE'S OWN answer, kept apart from the outcome on purpose. The
        # sender gate below REPLACES `decision`, so recording only the final
        # disposition would lose which policy matched — and `policy_error` is the
        # value that most needs to be visible: the gate degrades closed (R-5), so
        # a classifier outage renders exactly like `no_trigger` unless the trace
        # says otherwise.
        annotate(**{"nora.engage_reason": decision.reason})

        # The SENDER gate. Only consulted once something would actually be
        # answered, so an unrecognised sender in an allowed room is still
        # audited — which is exactly what `nora persons pending` reads.
        if decision.engage and not self._allowlist.is_allowed(
            platform=message.platform,
            conversation_id=message.conversation_id,
            person_id=person.person_id if person else None,
        ):
            m.inc("nora_denied_senders_total", platform=message.platform)
            log.warning(
                "tagged by a sender who is not allowed in this conversation "
                "(platform=%s person=%s) — recorded, not answered",
                message.platform,
                person.person_id if person else "<unmapped>",
            )
            decision = EngageDecision(engage=False, reason="sender_not_allowed")
            _outcome(m, message, "sender_not_allowed")
        else:
            # The ambient majority ends `recorded`, and it is the ending that had
            # NOTHING before this change — a message that passed cleanly produced
            # a root with a room key and six children saying only that they ran.
            _outcome(m, message, "engaged" if decision.engage else "recorded")

        return IngestResult(message=message, decision=decision)

    async def _run_edit(
        self, *, message: NormalizedMessage, original: EditedMessage,
    ) -> IngestResult:
        """An edit whose original is on record (design/message-edits/02 §4).

        The ambient path's stages, in its order, with three differences:
        the record is a `message_edit` row and is skipped when the text did not
        change; Honcho is fed a framed correction; and the gate asks whether
        the edit NEWLY addresses her (`edit_gate`). No standing event: the
        standing fold counts new messages, and an edit is not one.

        A turn it opens is answered AS THE ORIGINAL — its timestamp and its
        platform handle — because that is the message the room sees, and what
        the 👀 reaction and a threaded reply have to point at. A WhatsApp edit's
        own stanza id is not a message anyone can react to."""
        m = self._metrics
        with span("ingest.identity"):
            person = await identity_stage.run(
                message=message, persons=self._persons, metrics=m
            )
        annotate(**{"nora.person_known": person is not None})
        async def _custody(msg: NormalizedMessage) -> NormalizedMessage:
            return await media_stage.run(
                message=msg, sources=self._media_sources, blobs=self._blobs,
                media_store=self._media_store, metrics=m,
                stickers=self._stickers, transcriber=self._transcriber,
                comprehension=self._comprehension,
                withdrawn=self._withdrawn_gate(msg, person),
                person=person, language=self._language,
            )

        with span("ingest.media"):
            message = await edit_media_stage.run(
                message=message, original=original, custody=_custody,
            )
        with span("ingest.edit_record"):
            recorded = await edit_record_stage.run(
                message=message, original=original, audit=self._audit, metrics=m,
            )
        if recorded != "recorded":
            _outcome(m, message, "edit_unchanged" if recorded == "recorded_unchanged" else recorded)
            return IngestResult(message=None, decision=None)
        # A held delete that names this message BY THIS EDIT (Signal names an edit by its
        # own timestamp, and the delete may have arrived before the edit did) or the
        # original: applied now, before anything reads or feeds the edit on. The same
        # rule as the message path, asked about the original and the edit just recorded.
        if self._delete_withdrawer is not None:
            about_original = NormalizedMessage(
                platform=message.platform, conversation_id=message.conversation_id,
                conversation_type=message.conversation_type, sender_id=original.sender_id,
                sender_name=None, timestamp=original.ts, body="", raw={},
                message_id=original.platform_message_id,
            )
            held = await self._apply_orphans(
                about_original, original.person_id, original.id,
                edit_timestamps=(message.timestamp,),
            )
            if held != "none":
                _outcome(m, message, _held_outcome(held))
                return IngestResult(message=None, decision=None)
        # The one guard, for the edit path too: a delete may have hidden the original
        # since `edit_target` read it, and nothing below may feed or open a turn on it.
        stopped = await self._guard(original.id, message)
        if stopped is not None:
            _outcome(m, message, stopped)
            return IngestResult(message=None, decision=None)
        # Only an edit that CHANGED what a reader sees is a stimulus: an
        # unchanged one stopped above, and would wake nothing new.
        if self._stimulus_wiring is not None:
            with span("ingest.stimulus"):
                await stimulus_stage.run(
                    message=message, item="edit", row_id=original.id,
                    person_id=person.person_id if person else original.person_id,
                    wiring=self._stimulus_wiring, metrics=m, target=original,
                )
        with span("ingest.memory_feed"):
            await memory_feed_stage.run(
                message=message, person=person, memory=self._memory, metrics=m,
                spawn=self._spawn, edited=original,
            )
        with span("ingest.gate"):
            decision = await edit_gate_stage.run(
                message=message, previous=original, policy=self._policy, metrics=m,
            )
        annotate(**{"nora.engage_reason": decision.reason})
        if decision.engage and not self._allowlist.is_allowed(
            platform=message.platform,
            conversation_id=message.conversation_id,
            person_id=person.person_id if person else None,
        ):
            m.inc("nora_denied_senders_total", platform=message.platform)
            decision = EngageDecision(engage=False, reason="sender_not_allowed")
        _outcome(m, message, "edit_engaged" if decision.engage else "edit_recorded")
        answered_as = dataclasses.replace(
            message, timestamp=original.ts,
            message_id=original.platform_message_id,
        )
        return IngestResult(message=answered_as, decision=decision)

    async def _run_touch(self, *, message: NormalizedMessage, touch: Touch) -> IngestResult:
        """A reaction or a delete/unsend (design/cognition-loop/20 §1):
        found, recorded beside its target, raising a stimulus — never a turn,
        never fed to Honcho, never a transcript line. A delete also withdraws
        its target, or, when its target is not on record, is kept durably until it
        is (`touch_stage.run`). `touch` decides and records; this only sequences."""
        await self._record_touch(message=message, touch=touch)
        return IngestResult(message=None, decision=None)

    async def _guard(self, row_id: int, message: NormalizedMessage) -> IngestOutcome | None:
        """THE ONE FAIL-CLOSED POINT for the window between a message's audit write and the
        stages that read it. Immediately before an audited message is handed to any
        downstream consumer (the memory feed, the stimulus, the Standing and note stages,
        the gate) its row is read again, one read by primary key, and a hidden row stops the
        pipeline here: a delete that hid the row since the audit write, one whose orphan the
        delete's own second lookup discarded, one applied by another task.

        It closes THAT window and no other. A delete that arrives after this read is the
        ordinary after-the-fact case: it hides the row for every later read, and what was
        already fed or answered stays so (Honcho cannot forget what it was fed, design 04
        §7 (A2)); no reply path re-checks the flag before sending.

        A read that fails is asked ONCE more. If it fails again the message stops
        (`guard_read_failed`, counted), deliberately: no delete is known to exist, but the
        database is failing, and a message fed on a guess is the worse way to be wrong. A
        single transient failure costs nothing. `None` when it may go on."""
        for attempt in (1, 2):
            try:
                hidden = await self._audit.is_hidden(row_id=row_id)
            except Exception:
                log.exception(
                    "could not read whether message row %d is hidden (attempt %d of 2)",
                    row_id, attempt,
                )
                continue
            return "withdrawn" if hidden else None
        self._metrics.inc("nora_errors_total", stage="guard", platform=message.platform)
        return "guard_read_failed"

    async def _apply_orphans(
        self, message: NormalizedMessage, person_id: str | None, row_id: int,
        edit_timestamps: tuple[int, ...] = (),
    ) -> str:
        """Apply the held deletes that name the message just audited (`row_id`), by the
        one rule every place a delete takes effect shares. `none` when nothing named it;
        `applied` when every match was; `failed` when a KNOWN match could not be applied
        (the held delete stays for the start-up sweep, counted `apply_failed`, the message
        must NOT go on); `unreadable` when the held deletes could not be read twice
        running (no delete is known to exist: a deliberate, counted stop,
        `guard_read_failed`). A single failed read is asked again and costs nothing.

        DIRECTLY against `row_id`: no second lookup (a lookup that failed there
        used to leave the message flowing on and write a second orphan row for a delete
        already matched), no replay through the touch stage. The delete's own record is
        written first, then the hide and the cascade, and the held delete is discarded
        only AFTER (a crash in between leaves the row, and the sweep applies it
        again, idempotently). `edit_timestamps` are the timestamps of the message's edits
        (the edit path: a Signal delete may name an edit by its own)."""
        orphans = None
        for attempt in (1, 2):
            try:
                orphans = await self._audit.match_orphan_deletes(
                    message=message, person_id=person_id, edit_timestamps=edit_timestamps,
                )
                break
            except Exception:
                log.exception("held deletes could not be read (attempt %d of 2)", attempt)
        if orphans is None:
            self._metrics.inc(
                "nora_errors_total", stage="orphan_match", platform=message.platform,
            )
            return "unreadable"
        verdict = "none"
        for orphan in orphans:
            ok = await self._apply_orphan(orphan, row_id, message)
            verdict = "applied" if ok and verdict != "failed" else "failed"
        return verdict

    async def _apply_orphan(
        self, orphan: OrphanDelete, row_id: int, message: NormalizedMessage,
    ) -> bool:
        withdrawer = self._delete_withdrawer
        if withdrawer is None:
            return False
        delete = NormalizedMessage(
            platform=orphan.platform, conversation_id=orphan.conversation_id,
            conversation_type=message.conversation_type, sender_id=orphan.deleter_id,
            sender_name=None, timestamp=orphan.event_ts, body="",
            # The deleter's id forms ride in the stored event, as the touch stage's own
            # record does, so the authorship rule can read them back (`unforget`, the sweep).
            raw={DELETER_IDS_KEY: list(orphan.deleter_ids), "from_orphan": orphan.id},
        )
        try:
            await self._audit.append_delete(
                message_id=row_id, message=delete, person_id=orphan.deleter_person_id,
            )
        except Exception:
            log.exception("the delete of message row %d could not be recorded", row_id)
            self._count_withdrawal(message.platform, "apply_failed")
            return False                            # the held delete stays
        try:
            withdrawal = await withdrawer.withdraw(
                message_id=row_id, now_ms=int(time.time() * 1000),
            )
        except Exception:
            log.exception("delete withdrawal of message %d raised", row_id)
            withdrawal = DeleteWithdrawal(outcome="cascade_failed")
        self._count_withdrawal(message.platform, withdrawal.outcome)
        # The record is committed, so the held delete has done its job either way: a
        # failed hide is retried (and swept) from the record.
        try:
            await self._audit.discard_orphan_delete(orphan_id=orphan.id)
        except Exception:
            log.exception("held delete %d could not be discarded", orphan.id)
        if withdrawal.outcome == "cascade_failed":
            self._spawn(self._retry_withdrawal(message_id=row_id, platform=message.platform))
            return False
        self._count_withdrawal(message.platform, "applied_from_orphan")
        return True

    async def _record_touch(
        self, *, message: NormalizedMessage, touch: Touch,
    ) -> touch_stage.TouchResult:
        """One touch, found, recorded and (a delete) withdrawn; the stimulus rides
        behind it. The ending is the caller's to read."""
        m = self._metrics
        with span("ingest.touch"):
            done = await touch_stage.run(
                message=message, touch=touch, audit=self._audit,
                persons=self._persons, metrics=m,
                withdrawer=self._delete_withdrawer,
            )
        if done.row_id is not None and self._stimulus_wiring is not None:
            with span("ingest.stimulus"):
                await stimulus_stage.run(
                    message=done.message, item=touch.kind, row_id=done.row_id,
                    person_id=done.person_id, wiring=self._stimulus_wiring,
                    metrics=m, target=done.target,
                )
        if (
            done.withdrawal is not None and done.withdrawal.outcome == "cascade_failed"
            and done.target is not None
        ):
            self._spawn(self._retry_withdrawal(
                message_id=done.target.id, platform=message.platform,
            ))
        _outcome(m, message, done.ending)
        return done

    async def _retry_withdrawal(self, *, message_id: int, platform: str) -> None:
        """A withdrawal that failed is tried again, a bounded number of times
        (`withdrawal_retry_delays`), off the listener's path. The delete's record
        is already committed, so what is left after the last attempt is found by
        the start-up sweep. Logs ids and counts only."""
        withdrawer = self._delete_withdrawer
        if withdrawer is None:
            return
        for delay in self._withdrawal_retry_delays:
            await asyncio.sleep(delay)
            try:
                result = await withdrawer.withdraw(
                    message_id=message_id, now_ms=int(time.time() * 1000),
                )
            except Exception:
                log.exception("retry of the withdrawal of message %d raised", message_id)
                result = DeleteWithdrawal(outcome="cascade_failed")
            if result.outcome == "cascade_failed":
                self._count_withdrawal(platform, "cascade_failed")
                continue
            self._count_withdrawal(platform, "reconciled")
            return
        log.error(
            "withdrawal of message %d failed after %d retries; the start-up sweep "
            "will try again", message_id, len(self._withdrawal_retry_delays),
        )

    def _count_withdrawal(self, platform: str, outcome: str) -> None:
        try:
            self._metrics.inc(
                "nora_delete_withdrawals_total", platform=platform, outcome=outcome,
            )
        except Exception:  # an observation must never stop an ingest
            log.debug("delete withdrawal outcome %r not counted", outcome, exc_info=True)

    def _withdrawn_gate(
        self, message: NormalizedMessage, person: Any,
    ) -> Callable[[], Awaitable[bool]] | None:
        """Is this message withdrawn NOW? Asked by the spawned comprehension job
        just before its vision call and again before its Honcho feed: that job is
        scheduled by the custody stage before the row exists, so the delete can
        land at any point after (held, then hidden, or already hidden). True when
        a delete for it is held, or its row is hidden. It RAISES when it cannot
        look; the job decides what that means (`MediaComprehension`).
        `None` when no withdrawer is wired."""
        if self._delete_withdrawer is None:
            return None
        person_id = person.person_id if person is not None else None
        edit = message.edit_of
        if edit is not None:
            target = edit
        elif message.message_id:
            target = EditTarget(message_id=message.message_id)
        else:
            target = EditTarget(sent_ts=message.timestamp)

        async def withdrawn() -> bool:
            # A delete held for it (the durable `orphan_delete`, which stays until
            # the delete has been applied: no moment when it is neither held nor
            # hidden), or its row hidden. A lookup that FAILS raises: the caller
            # distinguishes «could not check» from «withdrawn».
            if edit is None and await self._audit.match_orphan_deletes(
                message=message, person_id=person_id,
            ):
                return True
            found = await self._audit.find_edited(
                conversation_id=message.conversation_id, target=target,
                sender_id=message.sender_id,
            )
            return found is not None and found.hidden

        return withdrawn
