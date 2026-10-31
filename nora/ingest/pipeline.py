"""`IngestPipeline` — runs the ambient stages in order.

A readable table of contents, not a decision-maker: every branch belongs to
the stage that owns it (`nora.ingest.stages.*`). This module only sequences
the stage calls and assembles the result — nothing here inspects message
content, catches an exception, or decides anything.

`run` takes an already-normalized `message`, not a raw event: parsing happens
on the adapter itself (`nora.platform.signal.adapter.SignalAdapter.listen`),
which drops anything that parses to `None` — a DM, a sync message, a self-send,
an empty body — before this pipeline ever sees it. `PlatformAdapter`
deliberately has no `parse()` (`nora.domain.ports`'s module docstring), so
`IngestPipeline` is not the place that could do this parsing generically.

`message=None` in the returned `IngestResult` means either that the
conversation is not allowlisted (the scope stage refuses BEFORE the audit, so
a denied room is never recorded — see that module for why the room and the
sender are enforced in different places) or that the audit write failed (the
audit stage drops rather than re-raises). A message that made it past that is
always paired with a `decision`, even for an unmapped sender (D6) or a policy
error (the gate stage degrades closed rather than propagating).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, get_args

from nora.domain.models import EditedMessage, NormalizedMessage, PersonRef, Touch
from nora.domain.ports import (
    BlobStore,
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
from .stages import external_files as external_files_stage
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
#: vocabulary of `nora_ingest_outcomes_total`, which is where an operator
#: READS these (R-76): the ingest trace is plumbing and is not exported, so the
#: counter is the live instrument and the span attribute is latent.
#:
#: The ambient path has five endings: the two refusals stop early, and the
#: third `return` covers the three dispositions a message that got all the way
#: through can have. An exception is not an ending here on purpose:
#: `nora.obs.tracing.span` already stamps `nora.outcome` with the exception's
#: type name and sets the span status to ERROR, and a name this module also
#: owned would let a crash be reported as one of these.
IngestOutcome = Literal[
    "scope_refused",        # the room is not allowlisted; nothing was recorded
    "audit_refused",        # the audit write failed; the message was dropped
    "sender_not_allowed",   # recorded, and deliberately not answered
    "engaged",              # recorded, and handed to the engagement path
    "recorded",             # recorded, and that is all — the ambient majority
    # The EDIT path. An edit is not a message, so
    # it gets its own endings rather than borrowing `recorded`/`engaged`: a
    # rate of edits is a different question from a rate of messages.
    "edit_recorded",        # folded onto the message it edits
    "edit_engaged",         # folded, and it newly addresses her — a turn
    "edit_unchanged",       # text and mentions unchanged; no turn, no feed
    "edit_stale",           # older than the edit on record (a redelivery); nothing written
    "edit_orphaned",        # no original on record; dropped, whoever sent it
    "edit_refused",         # the original is hers, or its author is not the editor
    # REACTIONS and DELETES:
    # Record items attached to the message they touch, never messages, so
    # they get their own endings for the edit path's reason.
    "reaction_recorded",    # recorded beside the message it reacts to
    "reaction_orphaned",    # no message on record to attach it to; dropped
    "delete_recorded",      # recorded beside the message it deletes
    "delete_orphaned",      # no message on record to attach it to; dropped
    "delete_refused",       # an author-only delete naming someone else's message
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


@dataclass(frozen=True)
class IngestResult:
    message: NormalizedMessage | None
    decision: EngageDecision | None
    # The stage that ended the run — lets a durable caller tell a legitimate
    # silence (scope_refused, edit_orphaned) from a swallowed audit FAILURE,
    # which must never retire as an intentional skip. Empty on pre-bridge
    # callers that never set it.
    outcome: str = ""
    # Set when durable=True so the caller can persist follow-on obligations
    # (stimulus/memory_feed run as durable work instead of inline awaits).
    row_id: int | None = None
    person: PersonRef | None = None
    # The admission-time absorption fact (Conversation.absorbed as the canonical
    # allowlist read it at ingest). A durable caller defers memory/stimulus by
    # minutes; the fact must travel with the message so a later recheck can
    # tell a room that was never absorbed from one whose policy moved.
    absorbed: bool = True
    # The room's unit at admission (`Conversation.unit`). Absorption is not
    # the only fact a config replacement can move: if the room's unit binding
    # changed while the obligation waited, the deferred write must refuse —
    # routing old content into a new unit is a disclosure, not a delay.
    unit: str | None = None


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
        SPAWNED read: `None` — the inert default, and every
        offline/eval composition — leaves every attachment with a bare handle
        and nothing MODEL-READ. It does NOT silence the voice-note lane: the
        custody stage writes an STT transcript to `media_objects.content`
        itself, inline, because those words were captured synchronously and
        holding them behind a job that may not exist discarded them outright.
        Present, each held blob additionally gets one model pass whose three
        fields land in `media_objects` and in Honcho, off this path.

        `transcriber` is `[speech.stt]`'s wiring state, the same shape as
        `stickers`: `None` (deployment default) records a voice note with a
        bare handle, with no transcript. When supplied, voice
        notes are transcribed inline during custody and their transcript rides
        the body beside the handle. `language` is the BCP-47 hint forwarded to
        the transcriber.

        `stimulus_wiring` is the stimulus stage:
        `None` — the default, `[cognition] enabled = false`, and every offline/
        eval composition — writes no stimulus. Present, every recorded
        person-authored item (a message, a changed edit, a reaction, a delete)
        appends one stimulus row with its cues.
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

    async def run(self, *, message: NormalizedMessage,
                  tx_hook: Any = None, durable: bool = False) -> IngestResult:
        """`message` is already normalized — the caller (the adapter's `listen()`
        loop, via `nora.app.dispatch.Dispatcher.on_message`) never hands this a
        `None`. The `nora_messages_total` increment happens here, once per
        normalized message."""
        # THE ROOT, and what it carries is the point: without these attributes an
        # ambient message would render in Langfuse as a trace with six named
        # children and no statement of what the ingest DID.
        # These four are what tell one ingest from another WITHOUT expanding a
        # child, and `nora.outcome` joins them on the way out.
        #
        # R-73 — A TRACE IS AN EGRESS. Everything here is a room-scoped or
        # structural fact: no sender id, no display name, no message body, no
        # coordinate. `conversation_id` is the one borderline value and it is the
        # repo's own definition that settles it (`scan_identifiers` returns None
        # for both the Signal base64 and the Discord snowflake form); a test
        # asserts that of every attribute on every span this opens.
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
        # entry point in the system, and most of its messages are ones Nora was
        # never going to answer, so this one attribute is the difference
        # between a collector an operator can read and one swamped by them. What
        # the trace would carry lives in `nora_ingest_outcomes_total`, which
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
            return await self._run(message=message, tx_hook=tx_hook,
                               durable=durable)

    async def _run(self, *, message: NormalizedMessage,
                   tx_hook: Any = None, durable: bool = False) -> IngestResult:
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
        # Messages and edits. A reaction or a delete is not a message: it is
        # counted on `nora_ingest_outcomes_total` (`reaction_*`, `delete_*`),
        # never here.
        if message.touch is None:
            m.inc("nora_messages_total", platform=message.platform)

        # The ROOM gate, before anything is written: a conversation the
        # operator has not allowlisted is not recorded at all.
        with span("ingest.scope"):
            admitted = await scope_stage.run(
                message=message, policy=self._allowlist, persons=self._persons, metrics=m,
            )
        if not admitted:
            # Ends here, and ENDS VISIBLY: without the attribute an unallowlisted
            # room is a trace that simply stops, indistinguishable from a crash.
            _outcome(m, message, "scope_refused")
            return IngestResult(message=None, decision=None,
                                outcome="scope_refused")
        # Whether this room's traffic is absorbed into memory/ambient
        # processing (Honcho feed, stimulus). `Conversation.absorbed` is the
        # one definition — a mention-only server-entry channel answers tags
        # and keeps its transcript without either side effect.
        room = self._allowlist.conversation(message.platform, message.conversation_id)
        absorbed = room is not None and room.absorbed

        # AN EDIT takes its own path from here: it is recorded beside the
        # message it changes, never as a message of its own — and an edit of
        # nothing on record is dropped, not ingested.
        if message.edit_of is not None:
            with span("ingest.edit_target"):
                found = await edit_target_stage.run(
                    message=message, target=message.edit_of, audit=self._audit,
                    persons=self._persons, metrics=m,
                )
            if found.ending is not None or found.original is None:
                _outcome(m, message, found.ending or "edit_orphaned")
                return IngestResult(message=None, decision=None,
                                    outcome=found.ending or "edit_orphaned")
            return await self._run_edit(
                message=found.message, original=found.original,
            )

        # A REACTION or a DELETE:
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
                # `person` is resolved above and rides in ONLY so a spawned
                # comprehension can be fed to Honcho under the sender's own
                # peer (D6, mapped senders only). The custody stage neither
                # resolves identity nor reads it for anything else.
                person=person, language=self._language,
            )
        # Shared file links become handles in the record: pure, no network, on
        # every platform, and before the audit so the refs land in the same
        # INSERT as the row.
        with span("ingest.external_files"):
            message = await external_files_stage.run(message=message)
        # A reply to an EDITED message quotes the revision's timestamp on
        # Signal; point it at the row the transcript holds before it is
        # written, so every reader resolves the parent against the transcript.
        with span("ingest.quote_target"):
            message = await quote_target_stage.run(
                message=message, audit=self._audit, metrics=m,
            )
        decision: EngageDecision | None = None
        if durable:
            # Under `durable` the gate runs BEFORE the audit: the caller's
            # `tx_hook` writes its handoff snapshot inside the row's own
            # commit, and the snapshot must carry the admission decision —
            # otherwise a crash between audit and handoff could only resume by
            # re-deciding (a second model admission). The gate is a pure
            # function of the message and policy — nothing it reads exists
            # only after the row — so the order is interchangeable here.
            decision = await self._decide(message=message, person=person, m=m)
        with span("ingest.audit"):
            row_id = await audit_stage.run(
                message=message, audit=self._audit, metrics=m, person=person,
                tx_hook=tx_hook, absorbed=absorbed, decision=decision,
                unit=room.unit if room is not None else None,
            )
        if row_id is None:
            _outcome(m, message, "audit_refused")
            return IngestResult(message=None, decision=None,
                                outcome="audit_refused")
        # The STIMULUS, right behind the Record row it names. Inside the
        # guard: a span for a stage that cannot run would claim work that never
        # happened.
        # Under `durable` these two stages are NOT run inline: the caller
        # (bridge WorkRunner) persists them as obligations in the same commit
        # that retires the ingest work, so the post-audit crash window cannot
        # silently lose them. Inline stays the non-bridge behaviour.
        if not durable:
            if self._stimulus_wiring is not None and absorbed:
                with span("ingest.stimulus"):
                    await stimulus_stage.run(
                        message=message, item="message", row_id=row_id,
                        person_id=person.person_id if person else None,
                        wiring=self._stimulus_wiring, metrics=m,
                    )
            with span("ingest.memory_feed"):
                await memory_feed_stage.run(
                    message=message, person=person, memory=self._memory,
                    metrics=m, spawn=self._spawn, absorbed=absorbed,
                )

            decision = await self._decide(message=message, person=person, m=m)
        if decision is None:
            raise AssertionError("gate did not run before the outcome")
        return IngestResult(message=message, decision=decision,
                            outcome="engaged" if decision.engage else "recorded",
                            row_id=row_id, person=person, absorbed=absorbed,
                            unit=room.unit if room is not None else None)

    async def _decide(self, *, message: NormalizedMessage,
                      person: PersonRef | None, m: Any) -> EngageDecision:
        """The gate plus the SENDER gate — one decision, called once per run
        whether it sits before the audit (durable) or after it (inline)."""
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
        # audited — which is exactly what `nora persons pending` reads. A
        # relevance-gate candidate counts as "would be answered": the quiescence
        # window will speak for it, so an excluded sender must not become one
        # (8765_44874). Without this the window's burst carries their words to
        # the gate and, on GO, into a turn.
        if (decision.engage or decision.relevance_gate) and not self._allowlist.is_allowed(
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
            # The ambient majority ends `recorded`, so a message that passed
            # cleanly still says how it ended.
            _outcome(m, message, "engaged" if decision.engage else "recorded")
        return decision

    async def _run_edit(
        self, *, message: NormalizedMessage, original: EditedMessage,
    ) -> IngestResult:
        """An edit whose original is on record.

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
        room = self._allowlist.conversation(message.platform, message.conversation_id)
        absorbed = room is not None and room.absorbed
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
        # Only an edit that CHANGED what a reader sees is a stimulus: an
        # unchanged one stopped above, and would wake nothing new.
        if self._stimulus_wiring is not None and absorbed:
            with span("ingest.stimulus"):
                await stimulus_stage.run(
                    message=message, item="edit", row_id=original.id,
                    person_id=person.person_id if person else original.person_id,
                    wiring=self._stimulus_wiring, metrics=m, target=original,
                )
        with span("ingest.memory_feed"):
            await memory_feed_stage.run(
                message=message, person=person, memory=self._memory, metrics=m,
                spawn=self._spawn, edited=original, absorbed=absorbed,
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
        """A reaction or a delete/unsend:
        found, recorded beside its target, raising a stimulus — never a turn,
        never fed to Honcho, never a transcript line. `touch` decides and
        records; this only sequences."""
        m = self._metrics
        with span("ingest.touch"):
            done = await touch_stage.run(
                message=message, touch=touch, audit=self._audit,
                persons=self._persons, metrics=m,
            )
        if done.row_id is not None and self._stimulus_wiring is not None:
            with span("ingest.stimulus"):
                await stimulus_stage.run(
                    message=done.message, item=touch.kind, row_id=done.row_id,
                    person_id=done.person_id, wiring=self._stimulus_wiring,
                    metrics=m, target=done.target,
                )
        _outcome(m, message, done.ending)
        return IngestResult(message=None, decision=None)
