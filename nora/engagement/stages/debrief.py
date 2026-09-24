"""Stage: debrief — a SECOND model reading the FINISHED exchange and revising
Nora's own position in the conversation
(`design/dreamer-agent-loop/26-standing.md`).

**It is a pipeline stage on the tag path, not a Dreamer adaptor** (§1), and
every other decision here follows from that one fact. Everything else in that
design bundle is background, unattended, on its own clock and currently switched
off, so a bug there costs nothing. This runs **inside the reply slot**, on the
live path, on every completed turn.

**A second model, and the argument is impartiality rather than cost** (§1). Not
the model that replied, reflecting: a different call, seeing the whole turn
*including its own outcome*. A model asked to judge its reply mid-turn is
judging under social pressure with the reply still to write. It is also why
Nora's own tools cannot do this — `ReplyGuard` cancels the next model call once
the terminal budget is spent, so **nothing in her own loop runs after the
reply**, and this stage has an after-the-reply path by construction.

**It runs on EVERY completed turn, not only the ones that sent something**
(§2). Success, an R-5 silence, a veto, a timeout — `outcome` carries which. The
temptation is to inherit `feedback.run`'s *"if sent and there is a tagger"*
gate, and it would be wrong for this bundle's own reason: a turn that
deliberately said nothing produced just as much reasoning as one that replied,
and a deliberate silence is the decision most likely to be wrong in a way
nobody complains about. A debrief that only reviews successful replies measures
the path that already works.

## The three things this module must not get wrong

**1. It NEVER raises.** Not a value, not a promise: the whole body is inside
one `try` whose `except` is `BaseException`, and `run` has no `raise` in it at
all. `except Exception` would leave the cancel path open — `CancelledError` is
not an `Exception` — and a cancellation escaping here reaches
`EngagementPipeline._run_admitted`'s own outer handler, which counts
`nora_silent_failures_total{reason="handler_error"}` and logs a silent failure
**on a turn that delivered a complete reply**. A false alarm on a working turn
is worse than the debrief simply not running.

**2. The turn is marked completed BEFORE this is awaited** — that is the call
site's job (`_run_admitted`'s `finally`), stated here too because the failure it
prevents is invisible from either side alone: an escape after successful
delivery would leave `completed` False, and `deliver.truncation_reason` would
then report a partial reply on a turn that sent a whole one.

**3. It holds the slot, so EVERY await in it is bounded — all of them.** This
docstring previously said "bounded twice" and named only the model call, and
that was false in the way that matters: `store.read` and `store.write` sat
OUTSIDE the `wait_for`, `create_sql_engine` sets no `command_timeout`, and both
run while the per-conversation lock is held. A Postgres connection hanging
mid-query during the post-reply fold wedged that conversation indefinitely — on
a turn that had already delivered its reply successfully (found by independent
review, 2026-08-27, before the tier had run a single fold). The model call is
bounded by `[standing] timeout_seconds`; the store calls (four now, see
`STORE_CALLS_PER_FOLD` below) by `[standing] store_timeout_seconds`; and the
SUM is asserted at startup against
`[standing] max_slot_hold_seconds` rather than argued in a comment — see
`nora.config.Config._the_standing_slot_hold_fits_its_budget`, which exists
because the comment here was wrong by 2x and nothing checked it.

The cost is user-visible — **but not to the person
being reviewed**, who already has their reply. It lands on the **second person
queued behind a busy conversation**, which is why the gate is
`nora_debrief_seconds` p95 ≤ 5s and why that series is deliberately **separate**
from `nora_tag_latency_seconds`: folding it in would move an existing SLO series
by changing what it measures, which is a broken gate wearing a passing number.

## The fold

**Was a plain `chat.completions.create`, never a tools-less Strands `Agent`,
until `4448c737` moved it onto one anyway.** The argument below was written
against exactly what this stage now does — `client.complete` (production:
`_DebriefModelClient`) calls `nora.agent.structured.ask_text`, which builds
`Agent(model=model, tools=[], callback_handler=None, ...)`: a tools-less
adaptor, the shape this paragraph's own rule (`03-no-agent-loop.md` — *if the
selection is knowable before the call it is an input; if it depends on what
the model concludes mid-reasoning it must be a tool*, and the debrief's whole
input set is knowable in advance) argues has nothing to gain from being an
Agent. It moved anyway, for the tracing benefit `4448c737`/this stage's own
`_complete` docstring records — Strands' own `Tracer` instruments an Agent
invocation for free, which is what let the hand-rolled `generation()` span
this file used to wrap the call in be deleted (2026-09-19) rather than kept
as the only thing tracing it. This is a live, unresolved tension between two
correct arguments, not a stale sentence to quietly delete: the "no tool, no
Agent" rule above is still right on its own terms, and the move happened for
a reason orthogonal to it.

**`finish_reason` is checked explicitly in `_check`, and it is now genuinely
reachable on a real cut-off.** `ask_text` does translate a NORMALLY-RETURNED
Strands `stop_reason` back into the raw-completion vocabulary
(`"max_tokens"` → `"length"`), but Strands' event loop does not always
return normally on a cut-off: `stop_reason == "max_tokens"` RAISES
`MaxTokensReachedException` unconditionally
(`strands/event_loop/event_loop.py:304-311`), so that translation alone could
never fire for a genuine cutoff. **FIXED 2026-09-19** — `ask_text`
(`nora/agent/structured.py`) now catches it the same way
`nora/agent/factory.py`'s `invoke` already does for the tag turn's Agent
invocation (measured incident, 2026-09-02, that motivated `factory.py`'s
catch: an uncaught `MaxTokensReachedException` there read as a generic
silent failure and dropped a recoverable partial reply), recovering the
partial text from `agent.messages` and returning
`StructuredResult(finish_reason="length", ...)` instead of letting the raise
reach `_complete`/`run`'s outer `except BaseException`, where it would have
counted as `nora_debrief_total{outcome="error"}` rather than the
`unavailable` this paragraph describes. `tests/unit/test_structured.py`
pins the catch, falsified by reverting it.

**The fold reads the current Standing before writing the next. This is the
non-negotiable** (§4), and it is the one requirement with live evidence behind
it: across 963 processed messages the Dreamer's entire output was two rows, both
wrong, both glossing the same thing — the second run knew MORE than the first
and still wrote a second wrong row beside it, because nothing in its write path
could see what was already there. `nora_debrief_prior_total` is what makes «this
fold read nothing» a reading rather than an assumption.

**Overflow refuses and consolidates; it never truncates** (§4.0). When the body
will not fit `[standing] max_chars`, the write is refused, the model is told the
cap and its own over-long answer, and asked to consolidate — one retry, inside
the same timeout. A second overflow writes nothing at all and the PRIOR position
stands, which is the fail-closed choice: a coherent stale position beats a
sentence cut in half. Silently dropping the oldest line is how a rolling summary
quietly becomes a recent summary.

**Everything is screened before the write, not before the read** (§4.3). The
trace holds raw tool results and a `where_is` result contains a live position,
so there is a path from a coordinate to a row that is injected into every
subsequent turn — persisting **by design** rather than by attack. R-18/R-28
screen egress from a *reply*; Standing is an *input*. `nora.agent.dream_screen`
is the screen that already exists for exactly this class of write, and screening
at read time would be the mistake F3 already refuted: the gate belongs where the
content class changes, and that is the write.

## What this stage does NOT do, and why

The design's §3 names four outputs. This stage produces **one**: `Standing`.

**It does not create notes, and it did until 2026-09-24.** 26 §1 put the note
tier's proposals on this stage only because it was the one path that ran after
the reply, not because a room's position owns them. They became the source of
52 of the 64 live notes: the fold never saw the unit's open notes, so it could
not tell a new question from one already tracked, and it could only create, so
every rewording was a new note that never got revised. It also wrote notes
about Nora's own turns (#199's class) into the group's notebook. Notes are
created by people on a tag and by the `note-candidates` pass, which reads the
index and revises instead of duplicating (#228); this stage compresses the
room's position and nothing else.

The third, the per-turn grounding verdict, was a second call from 2026-08-29
until 2026-09-24, and it is deleted along with both re-derivation triggers it
fed (`design/standing-compact/`). Across 406 turns it said `grounded=false` 25
times, 17 of them on a turn whose tool results it was shown cut to a fragment,
and its only consumer re-derived the position by withholding the prior from
the prompt: every conviction threw away what the room's position was holding.

The count has been wrong three times in this docstring. It is written out
rather than derived because there is nothing to derive it from, which is
exactly why it rots; the tests that count the store awaits are what actually
catch a new output appearing.

**FACT PROPOSALS ARE THE ONE THAT IS MISSING**, and they are Phase 4 in the
roadmap rather than an oversight here. The response schema below is
deliberately not widened in advance for them: an optional field nothing
validates is exactly the failure §7's two-shapes-with-a-discriminant decision
was made to avoid.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import tzinfo
from typing import Protocol

from opentelemetry.util.types import AttributeValue

from nora.agent.dream_screen import screen
from nora.domain.culture import CultureRevision
from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.domain.ports import RecordStore, StandingStore
from nora.domain.standing import STANDING_STORE_CALLS_PER_FOLD, Standing
from nora.domain.tag_trace import TagTrace
from nora.obs.metrics import Metrics
from nora.obs.tracing import annotate

from ..debrief_render import render_debrief_input

log = logging.getLogger("nora.engagement.debrief")


@dataclass(frozen=True)
class Completion:
    """One raw completion, with the field that decides whether it may be
    parsed at all.

    `finish_reason` rides here rather than being dropped because a raw call's
    cut-off is SILENT: `"length"` comes back with a truncated body and no
    exception, and a truncated JSON body can still parse into something. The
    two must arrive together or a caller will eventually check one and use the
    other.
    """

    content: str
    finish_reason: str


class ModelClient(Protocol):
    """The debrief's one collaborator that talks to a model.

    A documented structural type rather than a `nora.domain.ports` Protocol,
    on the rule that outlived the module that first stated it (the language
    tier's observer, deleted 2026-09-07): one consumer, one verb, and
    promoting it would commit a production implementation plus a fake to the
    shared contract-suite rule for a shape with nothing to share it with.
    `nora.engagement` may not import
    `nora.config` or build an OpenAI client, so the real one is constructed in
    the composition root and injected.

    It returns a `Completion` and not a `str`, which is the one place it
    differs from the shape it inherited — see `Completion`.
    """

    @property
    def model(self) -> str: ...

    async def complete(
        self, *, system: str, prompt: str,
        trace_attributes: Mapping[str, AttributeValue] | None = None,
    ) -> Completion: ...


class ObjectiveSource(Protocol):
    """One objective body by name, read fresh on every call.

    A SOURCE and not a string, for the reason the same shape was given in the
    language tier's observer (deleted 2026-09-07): the objective files are
    bind-mounted and hot-reload on mtime, so a
    stage handed the text at construction would keep sending whatever was on
    disk when the process started and an operator's edit would look like it did
    nothing. That argument is stronger here than anywhere else it has been made
    — this text is the only lever on what a position CONTAINS, and the cap on
    the row is already tunable for exactly the reason the text should be.
    """

    def objective_body(self, name: str) -> str: ...


#: The objective the STANDING call reads. `objective_body`, never
#: `for_objective`: the latter prepends the DREAMER's doctrine, which is
#: research-framed, and «a research-framed doctrine on a task with no research»
#: is one of the two named causes of the 4096-token runaway that made this a
#: plain completion in the first place.
OBJECTIVE = "standing-debrief"

#: How many BOUNDED STORE AWAITS one fold holds the conversation's slot for.
#: `nora.config` imports this to compute the worst-case hold, so the number
#: lives in the module that makes the calls rather than in the validator that
#: checks them.
#:
#: **It has been wrong three times.** It was written as 2 when only
#: `store.read` and `store.write` existed; the (since deleted) `TurnVerdict`
#: added a verdict save and S2's (since deleted) re-derivation trigger added a
#: verdict read, taking it to 4, and neither updated the sum — so the declared
#: ceiling was under the real worst case by ten seconds, with the next person
#: in that room waiting for it. The validator was the instrument built to
#: catch exactly that arithmetic and it was carrying the stale figure itself.
#:
#: It went 4 -> 5 when note proposals added `notes_sink.propose`, 5 -> 6 for
#: the watermark batch read (standing-watermark 20), and 6 -> 7 for the media
#: join (issue #116, 2026-09-08); the test below caught each within seconds of
#: the call being written — which is the whole point of having it rather than
#: a number in two places. It went back to 5 on 2026-09-24, when the turn
#: verdict's save and the re-derivation trigger's verdict read were deleted
#: with the verdict itself (design/standing-compact/), and to 4 the same day
#: when note proposals, and `notes_sink.propose` with them, were removed.
#:
#: `test_the_slot_hold_counts_every_store_await_the_stage_makes` counts the
#: `asyncio.wait_for` calls in `run` and asserts this matches, so adding a
#: fifth store call fails a test instead of silently widening the hold.
#: Re-exported from `nora.domain.standing`, where it lives so `nora.config`
#: can read it without importing this package (see its own comment there).
STORE_CALLS_PER_FOLD = STANDING_STORE_CALLS_PER_FOLD


async def _complete(
    client: ModelClient, *, prompt: tuple[str, str], call: str,
    tag_id: str, conversation_id: str,
) -> Completion:
    """The fold's model call.

    **No hand-rolled span here anymore (was issue #64's GENERATION span;
    removed as part of the strands.telemetry replacement).** `client.complete`
    is `_DebriefModelClient.complete` in production, which calls
    `nora.agent.structured.ask_text` — a real Strands `Agent` invocation since
    `4448c737` moved the fold and the standing tier onto the SDK transport.
    Strands instruments that itself: an agent span plus a `chat` model-invoke
    span underneath it, both carrying the SDK's own GenAI semconv and its
    native redaction lever (`design/13-trace-content-and-redaction.md`) — the
    same mechanism the tag path's own agent loop already relies on.

    Wrapping that in a SECOND, hand-rolled span (as this function used to)
    produced a redundant parent around Strands' own agent span for every
    fold/consolidate call — two nested, overlapping traces for one
    model call. That double instrumentation predates the SDK-transport move
    (this span existed when the debrief's model call genuinely had nothing
    else tracing it) and was never removed once the move made it redundant.

    `tag_id`/`conversation_id`/`call` still need to reach the resulting span,
    so they ride as `trace_attributes` — `ask_text`'s own parameter, which the
    SDK's event loop copies onto the agent span AND the model-invoke span
    underneath it (`strands/event_loop/event_loop.py`). `call` distinguishes
    fold/consolidate without a per-call span name — the same
    information issue #209's `f"engagement.debrief.{call}"` name used to
    carry, now a filterable attribute instead of a string to parse.
    """
    system, user = prompt
    return await client.complete(
        system=system, prompt=user,
        trace_attributes={
            "nora.tag_id": tag_id,
            "nora.conversation_id": conversation_id,
            "nora.debrief.call": call,
        },
    )


def _prompt(
    *, instructions: str, rendered: str, max_chars: int,
) -> tuple[str, str]:
    """`(system, user)` — the objective and its length rule in the SYSTEM
    role, the turn being folded in the USER role.

    They were one concatenated string. That is not merely untidy: the rendered
    half is a transcript of what people typed, so with both in one user
    message an instruction written by a member sits in the same role as the
    operator's, and nothing downstream can tell which is which.
    """
    return (
        f"{instructions}\n\n"
        f"The position you write must be at most {max_chars} characters.",
        rendered,
    )


def _consolidate_prompt(
    *, instructions: str, rendered: str, max_chars: int, over: str
) -> tuple[str, str]:
    """The retry, and it is a RETRY rather than a truncation.

    The model is shown its own over-long answer and the number it missed, which
    is what makes the failure model-resolvable: it can decide what to drop.
    Nothing here decides that for it — the moment this module picks, the tier
    has silently become a recency slice.

    **TWO ordering rules, and the SECOND one is the OVERRIDE.** The position
    prefers what Nora CONCLUDED over what the room said (issue #78 Phase 2's
    tiebreak reversal): room-said belongs in the transcript, and a shrink that
    drops her synthesis first leaves the tier carrying other people's words
    wearing her name. Recency alone cannot govern the tie, because the most
    recent thing in a position is just as likely to be a room quote as one of
    her judgements — so recency is scoped to a tie between two things of the
    SAME KIND, and the synthesis rule outranks it when the kinds differ.

    Since 2026-09-24 there is a THIRD kind between the two: what she FOUND on
    a turn and has not shared (design/standing-compact/10 §4). It ranks below
    what she concluded or owes, because it is the part of the position most
    harmless to lose, and above what the room said, because the transcript
    keeps the room's words and nothing keeps an unsent tool result. Without a
    rank it was always the most recent clause and so the last one dropped,
    which evicted older commitments first (review 2026-09-24).

    The rule ran the OPPOSITE way until 2026-08-29 — «what the room SAID
    outranks what you concluded about it» — and that phrasing is part of why a
    recap-shaped standing row survived: the shrink was taught to prefer exactly
    the clauses that made it a recap (the falsifier and the flip condition live
    in `design/standing-split-fold/20-ratchet-and-tiebreak.md`).
    """
    # `(system, user)`: the objective, the length rule and the consolidation
    # rules are the OPERATOR's and go in the system role; the turn and the
    # model's own over-long answer are the material being worked on.
    return (
        f"{instructions}\n\n"
        f"The position you write must be at most {max_chars} characters.",
        f"{rendered}\n\n"
        f"Your previous answer was {len(over)} characters, which is over the "
        f"{max_chars}-character limit, so it was NOT saved. Consolidate it now "
        f"and answer again, within the limit. Drop the oldest and least "
        f"consequential thing in it, never the most recent. "
        f"What you CONCLUDED or COMMITTED to outranks what you FOUND and have "
        f"not shared, and what you found outranks what the room SAID: drop in "
        f"the reverse of that order, even when the thing you drop is the more "
        f"recent. Recency decides only between two things of the same "
        f"kind.\n\n"
        f"Your previous answer was:\n{over}"
    )


def _body(raw: str) -> str | None:
    """The `standing` string out of the model's JSON, or `None`.

    A missing key, a non-string value, a body that is only whitespace, or text
    that is not JSON at all are ONE outcome here (`malformed`) and deliberately
    not four: every one of them means the same thing operationally — nothing
    was written and the prior position stands — and splitting them would be
    four labels on a counter nobody could act on differently.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    body = parsed.get("standing")
    if not isinstance(body, str) or not body.strip():
        return None
    return body.strip()


@dataclass(frozen=True)
class Wiring:
    """Everything the stage needs, as ONE object that is either present or
    absent — never four optional parameters on the pipeline.

    The shape is the point. Four independently-defaulting collaborators let a
    deployment wire a store and forget the model client, and the result is a
    stage that runs, finds nothing to do, and reports nothing: the
    `unavailable`-that-looks-like-`nothing_found` failure this repo names
    R-27. `EngagementPipeline` holds `Wiring | None`, so «configured» and «not
    configured» are the only two states expressible, and `[standing] enabled`
    is what decides which one the composition root builds.

    `max_chars` is the OPERATING cap and is config
    (`nora.domain.standing.DEFAULT_STANDING_MAX_CHARS` is its default);
    `standing_body_fits_the_ceiling_check` in the database is the absolute
    ceiling underneath it that no config can pass. `fold_batch` is the fold's
    batch size: the fold reads `since_including_nora(after_id=watermark,
    limit=fold_batch)` through `record_store` — the NEXT `fold_batch` rows
    since the last fold's row-id watermark, not the LAST 40 rows of the reply
    path's window (design/standing-watermark/20, D8). The 40-row window is
    gone entirely.
    """

    store: StandingStore
    client: ModelClient
    objective: ObjectiveSource
    # The RECORD store, for the fold's watermark read: the tag fold reads
    # `since_including_nora(after_id=prior.last_folded_row_id, limit=fold_batch)`
    # — the conversation stretch since the last fold — rather than slicing the
    # reply path's window (design/standing-watermark/20).
    record_store: RecordStore
    max_chars: int
    fold_batch: int
    timeout_seconds: float
    store_timeout_seconds: float
    # The shared media join (issue #116). The fold renders these rows for a
    # model, so it needs the same answer to «what did that photo show» that
    # the reply path gets — on master the description rode in `messages.body`
    # and every consumer had it. `None` is the inert state and renders the
    # bare handles the row actually carries. LAST, because a defaulted field
    # cannot precede the ones above it.
    media_join: MediaJoin | None = None


async def run(
    *,
    trace: TagTrace,
    platform: str,
    store: StandingStore,
    client: ModelClient,
    objective: ObjectiveSource,
    metrics: Metrics,
    display_name: str,
    record_store: RecordStore,
    max_chars: int,
    fold_batch: int,
    timeout_seconds: float,
    store_timeout_seconds: float,
    now_ms: int,
    media_join: MediaJoin | None = None,
    tz: tzinfo | None = None,
    culture: CultureRevision | None = None,
) -> None:
    """Fold this finished turn into the conversation's position. **Never
    raises, and never returns anything** — a stage that runs after delivery has
    nothing to hand back to a turn that is already over, and anything it could
    signal would be a signal the caller must then decide what to do with on the
    live path.

    Every exit lands on exactly one `nora_debrief_total{outcome}` label, and the
    `finally` observes `nora_debrief_seconds` on all of them including the
    failures — a latency series that only records successes describes the path
    that already works, which is §2's argument applied to this module's own
    instrument.
    """
    started = time.perf_counter()
    outcome = "error"
    try:
        # THE NON-NEGOTIABLE (§4), and it is first for a reason: a store that
        # cannot be read is `unavailable`, and a fold against a guessed-empty
        # prior is exactly the two-contradictory-rows failure this whole tier
        # exists to not repeat. An ERROR is never a PASS.
        try:
            prior: Standing | None = await asyncio.wait_for(
                store.read(
                    platform=platform, conversation_id=trace.conversation_id,
                ),
                timeout=store_timeout_seconds,
            )
        except Exception:
            log.exception(
                "debrief: could not read the prior position (tag=%s); "
                "not folding", trace.tag_id,
            )
            outcome = "unavailable"
            return
        metrics.inc(
            "nora_debrief_prior_total",
            prior="read" if prior is not None else "absent",
        )

        # THE WATERMARK BATCH READ (design/standing-watermark/20). The fold no
        # longer slices the reply path's window: it reads the NEXT `fold_batch`
        # rows since the last fold's row-id watermark — including Nora's own
        # rows, which `since` excludes and which the fold needs to re-judge her
        # position. NULL watermark = read from the beginning (D5: no backfill —
        # a conversation that has never folded reads its history in batches).
        # A failed read is `unavailable`, never a fold against a guessed-empty
        # stretch: the whole point of the watermark is that the fold SEES what
        # arrived since it last folded, and a fold that skips the stretch is
        # the window defect this bundle exists to remove.
        try:
            batch: tuple[TranscriptRow, ...] = tuple(
                await asyncio.wait_for(
                    record_store.since_including_nora(
                        conversation_id=trace.conversation_id,
                        after_id=(
                            prior.last_folded_row_id
                            if prior is not None
                            and prior.last_folded_row_id is not None else 0
                        ),
                        limit=fold_batch,
                    ),
                    timeout=store_timeout_seconds,
                )
            )
        except Exception:
            log.exception(
                "debrief: could not read the watermark batch (tag=%s); "
                "not folding", trace.tag_id,
            )
            outcome = "unavailable"
            return
        if media_join is not None:
            # BOUNDED like every other store call the fold makes, and counted
            # in `STORE_CALLS_PER_FOLD`: this is a real read of
            # `media_objects` on a slot-held path, and a hold whose declared
            # ceiling omits one of its awaits is the arithmetic the validator
            # exists to catch (it caught this one the moment it was written).
            #
            # Its failure is NOT the read's failure, though, so it is outside
            # the `try` above: `enrich` degrades to the unmodified rows, and
            # folding it into that `except` would report `unavailable` — a fold
            # that did not happen — when what happened is a fold with thinner
            # evidence.
            try:
                batch = await asyncio.wait_for(
                    media_join.enrich(batch, path="debrief"),
                    timeout=store_timeout_seconds,
                )
            except Exception:
                log.exception(
                    "debrief: media join failed (tag=%s); folding on the bare "
                    "handles", trace.tag_id,
                )

        instructions = objective.objective_body(OBJECTIVE)
        # **The prior is ALWAYS rendered** (26 §4's non-negotiable). Until
        # 2026-09-24 a re-derivation withheld it here and the prompt then told
        # the model "no position yet — this is the first fold", which wrote a
        # fresh position over one that was holding threads open for days
        # (design/standing-compact/00-intent). Nothing withholds it now.
        rendered = render_debrief_input(
            trace=trace, prior=prior,
            display_name=display_name, metrics=metrics, since_rows=batch, tz=tz,
            # The room's account (DEC-204) — the one the turn itself read at
            # its start, handed through rather than read a second time.
            culture=culture,
        )
        # ONE `wait_for` around the whole fold, the consolidate retry
        # included: the budget the slot can afford is a total, and a retry
        # bounded on its own would add up to twice it on exactly the turn that
        # was already slow.
        async def _fold() -> tuple[str | None, str]:
            """The standing call, and its consolidate retry when the first
            answer does not fit, as `(body, refusal_label)`."""
            reply = await _complete(
                client, call="fold",
                tag_id=trace.tag_id, conversation_id=trace.conversation_id,
                prompt=_prompt(
                    instructions=instructions, rendered=rendered,
                    max_chars=max_chars,
                ),
            )
            first = _check(reply, trace.tag_id)
            if first[0] is None or len(first[0]) <= max_chars:
                return first
            metrics.inc("nora_standing_overflow_refusals_total", stage="first")
            # Its OWN span, not a second event on the first one. The retry is a
            # different prompt producing a different answer, and a trace that
            # folded them together would show one generation whose input does
            # not explain its output — the shape §4.0's consolidate exists to
            # make legible.
            retry = await _complete(
                client, call="consolidate",
                tag_id=trace.tag_id, conversation_id=trace.conversation_id,
                prompt=_consolidate_prompt(
                    instructions=instructions, rendered=rendered,
                    max_chars=max_chars, over=first[0],
                ),
            )
            return _check(retry, trace.tag_id)

        body, refusal = await asyncio.wait_for(
            _fold(), timeout=timeout_seconds)
        if body is None:
            outcome = refusal
            return

        if len(body) > max_chars:
            # The second refusal. Nothing is written and the PRIOR position
            # stands — a coherent stale position beats a sentence cut in half,
            # and truncating here would be the silent eviction §4.0 refuses.
            metrics.inc("nora_standing_overflow_refusals_total", stage="final")
            log.warning(
                "debrief: the fold would not fit %d characters even after "
                "consolidating (tag=%s); keeping the prior position",
                max_chars, trace.tag_id,
            )
            outcome = "refused_overflow"
            return

        # §4.3: screened BEFORE the write, because the write is where the
        # content class changes — this row is injected into every subsequent
        # turn until a later fold happens to drop it, so a coordinate reaching
        # it persists by design rather than by attack.
        kept, rejected = screen(
            adaptor="debrief", items=[{"standing": body}], metrics=metrics,
        )
        if not kept:
            log.warning(
                "debrief: the fold was screened out (tag=%s reason=%s); "
                "keeping the prior position",
                trace.tag_id, rejected[0].reason if rejected else "?",
            )
            outcome = "screened"
            return

        # Bounded and labelled `unavailable`, NOT `timeout`, exactly as the read
        # above is. `asyncio.TimeoutError IS TimeoutError` on every Python this
        # project supports (>= 3.11), so without this `except` a write that
        # timed out would fall through to `except TimeoutError` below and be
        # counted as «the fold did not finish inside its own budget» — on the
        # very series `[standing] timeout_seconds` is tuned against. A store
        # that could not be written is «I could not check», the same R-27 state
        # the read path was already careful about; reading that off the
        # debrief-budget series would send the next investigation at the model
        # when the problem is Postgres.
        # CONSTRUCTED OUTSIDE the `try` below, deliberately. `Standing(...)` is
        # an argument expression, so leaving it inside meant a domain-invariant
        # violation — `__post_init__` refusing a blank or over-ceiling body —
        # was caught by that `except` and counted `unavailable`, i.e. «the
        # store could not be written». "The model produced a body the domain
        # refuses" and "Postgres was unreachable" are different readings, and
        # conflating them is the R-27 substitution the `except` itself exists
        # to avoid. Unreachable today (`max_chars` cannot exceed the ceiling,
        # `_max_chars_stays_under_the_database_ceiling` sees to that) and
        # hoisting costs nothing, which is exactly when to fix it rather than
        # argue it is fine.
        revised = Standing(
            platform=platform,
            conversation_id=trace.conversation_id,
            body=body,
            # A plain count of folds on this row. It fed the fold-count
            # re-derivation ceiling until 2026-09-24; nothing resets it now.
            fold_count=(prior.fold_count + 1) if prior is not None else 1,
            updated_at=now_ms,
            # The ROW-ID watermark (design/standing-watermark/20), and it
            # advances on exactly ONE condition: a fold that read new rows.
            # Carried forward unchanged on a fold with nothing to read, and on
            # every refusal path above this write (which never reaches here at
            # all). Advanced to the last row the
            # fold actually read — the batch is ascending, so `batch[-1]` is
            # the furthest it saw; a batch capped at `fold_batch` leaves the
            # rest for the next fold (the rollup's job, 30-rollup.md).
            last_folded_row_id=(
                batch[-1].id if batch else (
                    prior.last_folded_row_id if prior is not None else None)
            ),
        )
        try:
            await asyncio.wait_for(
                store.write(standing=revised), timeout=store_timeout_seconds)
        except Exception:
            # **Says what it KNOWS, not what it assumes.** This used to log
            # "the prior position stands", which is a claim about database
            # state made without reading it back — on a `wait_for` timeout the
            # upsert is cancelled MID-STATEMENT and asyncpg gives us no way to
            # know from here whether it committed. If it did, the next fold
            # reads the new `fold_count` and watermark as though it had.
            #
            # The LABEL is still right: `unavailable` is precisely «I could not
            # check». It is the sentence that was overclaiming.
            log.exception(
                "debrief: the write of the revised position did not complete "
                "(tag=%s); whether it committed is unknown from here",
                trace.tag_id,
            )
            outcome = "unavailable"
            return
        outcome = "written"
    except TimeoutError:
        # Its own label rather than `error`, and its own branch rather than an
        # `isinstance` inside the one below: the debrief holds the slot, so a
        # timeout here is the failure whose cost lands on the next person in
        # the queue, and it is the reading `[standing] timeout_seconds` is
        # tuned against. Rolled into `error` it would be invisible under
        # whatever else went wrong that week.
        log.warning(
            "debrief timed out after %.1fs (tag=%s); keeping the prior position",
            timeout_seconds, trace.tag_id,
        )
        outcome = "timeout"
    except asyncio.CancelledError:
        # Its own label and its own LOG LEVEL, and both are about alert
        # fatigue rather than correctness.
        #
        # Swallowing this is design-mandated (26 §5: catch `BaseException`,
        # never re-raise) and is handled correctly by the branch below — but
        # every routine shutdown cancel of an in-flight tag then wrote an
        # ERROR-level "debrief failed" traceback and counted
        # `outcome="error"`, the one label that is supposed to mean SOMETHING
        # IS WRONG. A counter that fires on ordinary shutdown is a counter
        # nobody reads, which is this repo's own «a noisy gate is a disabled
        # gate», one series over.
        #
        # A cancellation is not a fault: the process is going away and the
        # position simply is not revised this turn. INFO, and a label that
        # says so. Nothing awaits after `outcome = "written"`, so a cancel
        # never lands on a committed position.
        log.info(
            "debrief cancelled (tag=%s); keeping the prior position",
            trace.tag_id,
        )
        outcome = "cancelled"
    except BaseException:
        # `BaseException`, and it is not defensive breadth — see the module
        # docstring's (1). `CancelledError` is not an `Exception`, and letting
        # one out of here reports a silent failure on a turn that delivered.
        # It is caught ABOVE rather than here so shutdown does not masquerade
        # as a defect; this branch is now genuinely «something unexpected».
        log.exception("debrief failed (tag=%s); keeping the prior position",
                      trace.tag_id)
    finally:
        metrics.observe("nora_debrief_seconds", time.perf_counter() - started)
        metrics.inc("nora_debrief_total", outcome=outcome)
        # The fold's verdict on the span the pipeline opened around this call.
        # `nora_debrief_total` already carries it as a series; the span is what
        # makes «this fold, on this tag» answerable — and it sits beside the
        # generation children, so a `refused_overflow` is one click from the
        # over-long body that caused it. In the `finally` because every one of
        # the eleven exits sets `outcome` and then leaves by a different door.
        #
        # `outcome` and nothing else: it is a local `str` that cannot raise on
        # read. `client.model` is an injected property, and this `finally` sits
        # past every handler in this function — a raise from someone else's
        # getter here would escape `run`, which the module docstring promises
        # can never happen. The model name is on the generation children, where
        # a raise is inside the guarded region.
        annotate(**{"nora.debrief.outcome": outcome})


def _truncated(completion: Completion, tag_id: str, *, what: str) -> bool:
    """The finish_reason-FIRST refusal.

    A `"length"` finish means the body is truncated, so it must never reach
    `json.loads`: a truncated JSON document can parse into something (a shorter
    string, a dict missing its last key) and that «something» is an ERROR
    wearing a PASS. `_check` calls this for the fold and for the consolidate
    retry alike, so the two cannot drift apart on it.
    """
    if completion.finish_reason == "length":
        log.warning(
            "debrief: %s was cut off at the output ceiling (tag=%s); not "
            "parsing a truncated body", what, tag_id,
        )
        return True
    return False


def _check(completion: Completion, tag_id: str) -> tuple[str | None, str]:
    """`(body, refusal_label)` for the STANDING call — `finish_reason` first.

    **The two failures carry DIFFERENT labels and that is the point**, even
    though both write nothing. `"unavailable"` is «the call was cut off, I
    could not check»; `"malformed"` is «the model answered and the answer was
    not the shape asked for». R-27's whole discipline is that those are not the
    same reading: a rising `unavailable` says the output ceiling or the input
    size is wrong, a rising `malformed` says the objective text or the schema
    is. Collapsing them would make the one instrument this stage has unable to
    tell an infrastructure problem from a prompt problem.
    """
    if _truncated(completion, tag_id, what="the fold"):
        return None, "unavailable"
    body = _body(completion.content)
    return (body, "") if body is not None else (None, "malformed")
