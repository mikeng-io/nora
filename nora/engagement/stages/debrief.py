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

**3. It holds the slot, so EVERY await in it is bounded — all four.** This
docstring previously said "bounded twice" and named only the model call, and
that was false in the way that matters: `store.read` and `store.write` sat
OUTSIDE the `wait_for`, `create_sql_engine` sets no `command_timeout`, and both
run while the per-conversation lock is held. A Postgres connection hanging
mid-query during the post-reply fold wedged that conversation indefinitely — on
a turn that had already delivered its reply successfully (found by independent
review, 2026-08-27, before the tier had run a single fold). The model call is
bounded by `[standing] timeout_seconds`; the two store calls by
`[standing] store_timeout_seconds`; and the SUM is asserted at startup against
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

**A plain `chat.completions.create`, never a tools-less Strands `Agent`.** A
tools-less adaptor is a completion wearing an agent's construction
(`03-no-agent-loop.md`): it inherits the loop machinery's failure modes for none
of its benefits. The rule that decides it is general — *if the selection is
knowable before the call it is an input; if it depends on what the model
concludes mid-reasoning it must be a tool* — and the debrief's whole input set
(the prior position, this turn's trace, the transcript) is knowable in advance.
There is no tool here, full stop.

**`finish_reason` is checked explicitly**, and that is the raw call's price of
admission. A Strands agent raises `MaxTokensReachedException` on a cut-off; a
raw completion does not — it returns `finish_reason="length"` and a **silently
truncated body**. Letting that reach a JSON parser is the R-27 substitution this
architecture exists to prevent, one layer down: a partial parse that happens to
succeed is an ERROR reported as a PASS. A `"length"` finish is `unavailable`,
never a fold.

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

## What this stage does NOT do yet, and why

The design's §3 names four outputs of one call. This stage produces **three**:
`Standing`, `TurnVerdict` and note proposals, each parsed from the same
completion with its own validated shape.

The count has been wrong twice in this docstring — it said "one" long after the
verdict landed forty lines below, and "two" in the same commit that added the
`notes_sink.propose` call, with a sentence left broken mid-edit. It is written
out rather than derived because there is nothing to derive it from, which is
exactly why it rots; the tests that count the store awaits are what actually
catch a fourth appearing.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import tzinfo
from typing import Any, NamedTuple, Protocol

from nora.agent.dream_screen import screen
from nora.agent.egress import scan_identifiers
from nora.agent.security import looks_like_coordinate
from nora.agent.tools import note_handle
from nora.domain.notes import NoteCreationProposal
from nora.domain.ports import DreamSink, Proposal, StandingStore, TurnVerdictStore
from nora.domain.provenance import instruction_sha256
from nora.domain.standing import STANDING_STORE_CALLS_PER_FOLD, Standing
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_verdict import TurnVerdict
from nora.obs.metrics import Metrics
from nora.obs.tracing import annotate, span

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
    following `nora.language.observer._ModelClient`'s stated precedent: one
    consumer, one verb, and promoting it would commit a production
    implementation plus a fake to the shared contract-suite rule for a shape
    with nothing to share it with. `nora.engagement` may not import
    `nora.config` or build an OpenAI client, so the real one is constructed in
    the composition root and injected.

    It returns a `Completion` and not a `str`, which is the one place this
    differs from `_ModelClient` — see `Completion`.
    """

    @property
    def model(self) -> str: ...

    async def complete(self, *, prompt: str) -> Completion: ...


class ObjectiveSource(Protocol):
    """One objective body by name, read fresh on every call.

    A SOURCE and not a string, for `nora.language.observer._ObjectiveSource`'s
    reason: the objective files are bind-mounted and hot-reload on mtime, so a
    stage handed the text at construction would keep sending whatever was on
    disk when the process started and an operator's edit would look like it did
    nothing. That argument is stronger here than anywhere else it has been made
    — this text is the only lever on what a position CONTAINS, and the cap on
    the row is already tunable for exactly the reason the text should be.
    """

    def objective_body(self, name: str) -> str: ...


#: The objective this stage reads. `objective_body`, never `for_objective`: the
#: latter prepends the DREAMER's doctrine, which is research-framed, and «a
#: research-framed doctrine on a task with no research» is one of the two named
#: causes of the 4096-token runaway that made this a plain completion in the
#: first place.
OBJECTIVE = "standing-debrief"

#: How many BOUNDED STORE AWAITS one fold holds the conversation's slot for.
#: `nora.config` imports this to compute the worst-case hold, so the number
#: lives in the module that makes the calls rather than in the validator that
#: checks them.
#:
#: **It has been wrong three times.** It was written as 2 when only
#: `store.read` and `store.write` existed; `TurnVerdict` added
#: `store_verdict.save` and S2's trigger added `store_verdict.latest`, and
#: neither updated the sum — so the declared ceiling was under the real worst
#: case by ten seconds, with the next person in that room waiting for it. The
#: validator was the instrument built to catch exactly that arithmetic and it
#: was carrying the stale figure itself.
#:
#: It went 4 -> 5 when note proposals added `notes_sink.propose`, and the
#: test below caught that within seconds of the call being written — which
#: is the whole point of having it rather than a number in two places.
#:
#: `test_the_slot_hold_counts_every_store_await_the_stage_makes` counts the
#: `asyncio.wait_for` calls in `run` and asserts this matches, so adding a
#: fifth store call fails a test instead of silently widening the hold.
#: Re-exported from `nora.domain.standing`, where it lives so `nora.config`
#: can read it without importing this package (see its own comment there).
STORE_CALLS_PER_FOLD = STANDING_STORE_CALLS_PER_FOLD


#: What lands on the span in place of text the egress floor refuses. It NAMES
#: the pattern that fired, because «the trace shows nothing» and «the trace
#: refused to show it» are different readings and only one of them is a bug.
_WITHHELD = "(withheld from the trace: {reason})"


def _trace_safe(text: str) -> tuple[str, str]:
    """`(what may go on the span, why the rest did not)`.

    **A trace is an egress.** The collector is a third party over the network,
    and `nora.agent.egress`'s own docstring says an identifier is «never safe to
    emit **or transmit**, whatever the destination». The debrief's prompt
    carries the turn's raw tool results — a `where_is` result is a live
    coordinate — so the two floor predicates that already guard every other
    write of that class run here too.

    **Reused, not reinvented**, and deliberately the same pair
    `nora.agent.dream_screen` calls rather than `screen` itself: `screen` counts
    `nora_dream_screened_total{adaptor}`, and a trace decision incrementing a
    Dreamer series would make that counter answer two questions at once.
    `scan_identifiers` and not `scan_reply`, for `dream_screen`'s stated reason
    — `scan_reply`'s model-name pattern matches nine ordinary English words and
    is only ever right for text addressed to the group.

    **Fails closed at whole-text granularity**, which is the known cost: one
    coordinate anywhere in a fold's prompt withholds the whole prompt. The
    provenance survives it — `nora.debrief.prompt_sha256` and `nora.tag_id` are
    stamped either way, and the turn's own `TagTrace` is retained in Postgres,
    so the fold is re-derivable by reference exactly when it is not readable
    inline. Splitting the render into per-block redaction is the better answer
    and is a larger change than this fix carries.
    """
    offence = scan_identifiers(text)
    if offence is not None:
        return _WITHHELD.format(reason=offence), offence
    if looks_like_coordinate(text):
        return _WITHHELD.format(reason="coordinate"), "coordinate"
    return text, ""


async def _complete(
    client: ModelClient, *, prompt: str, call: str, trace: TagTrace,
) -> Completion:
    """The fold's model call, as a GENERATION span (issue #64).

    Until this existed the Standing fold was the only model call in the process
    with no instrumentation at all: Strands traces the tag path's agent loop
    from the inside, and the debrief is deliberately NOT a Strands agent (this
    module's docstring says why), so nothing wrote a span for it. Counters said
    a fold took three seconds; nothing said what it read or what it answered —
    on the one tier whose output is injected into every later turn.

    **Hand-written rather than an OpenAI auto-instrumentor**, and the reason is
    this function's own body. An instrumentor hooks the SDK, so it would trace
    every `AsyncOpenAI` call in the process — the language observers, the media
    describers — and it would put the raw prompt on the wire with no screen in
    front of it. Both halves of that are wrong for this repo: the widening is
    unasked for, and the egress floor above is not optional.

    Both the OTel GenAI conventions and Langfuse's own attribute names are set.
    They are two spellings of one fact and neither is guessed: the conventions
    are what a future collector reads, `langfuse.observation.*` is what today's
    one renders a generation from.

    It is a CHILD of `engagement.debrief` by context, not by wiring — the
    pipeline opens that span around `debrief.run`, and `asyncio.wait_for` keeps
    the context whether it awaits in place or wraps a task.
    """
    body, withheld = _trace_safe(prompt)
    with span(
        f"engagement.debrief.{call}",
        **{
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": client.model,
            "langfuse.observation.input": body,
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": client.model,
            "nora.tag_id": trace.tag_id,
            "nora.conversation_id": trace.conversation_id,
            # The reference the withheld case still re-derives from: this digest
            # plus the retained `TagTrace` for `nora.tag_id` reconstructs the
            # exact prompt without the trace ever having carried it.
            "nora.debrief.prompt_sha256": instruction_sha256(prompt),
            "nora.debrief.prompt_withheld": withheld,
        },
    ):
        reply = await client.complete(prompt=prompt)
        out, out_withheld = _trace_safe(reply.content)
        # `finish_reason` on the span rather than only in `_check`'s branch: a
        # `"length"` finish is the R-27 substitution this stage exists to
        # refuse, and reading it off the trace is how an operator tells «the
        # model answered badly» from «the answer was cut off» without a log.
        annotate(**{
            "langfuse.observation.output": out,
            "gen_ai.response.finish_reasons": reply.finish_reason,
            "nora.debrief.output_sha256": instruction_sha256(reply.content),
            "nora.debrief.output_withheld": out_withheld,
        })
        return reply


def _prompt(*, instructions: str, rendered: str, max_chars: int) -> str:
    return (
        f"{instructions}\n\n"
        f"The position you write must be at most {max_chars} characters.\n\n"
        f"{rendered}"
    )


def _consolidate_prompt(
    *, instructions: str, rendered: str, max_chars: int, over: str
) -> str:
    """The retry, and it is a RETRY rather than a truncation.

    The model is shown its own over-long answer and the number it missed, which
    is what makes the failure model-resolvable: it can decide what to drop.
    Nothing here decides that for it — the moment this module picks, the tier
    has silently become a recency slice.

    **TWO ordering rules, and the second one was missing.** §4.0.1 states an
    asymmetry — when Standing must shrink, what the room SAID outranks what
    Nora concluded about it — and only the recency rule was ever written down.
    They conflict, which is why the instruction has to say which governs: the
    most recent thing in a position is often something SHE concluded, and the
    oldest is often something the room said. Recency alone therefore drops the
    room's own words first, which is the exact inversion §4.0.1 exists to
    prevent. So the room rule is stated as the OVERRIDE and recency is scoped
    to a tie between two things of the same kind.

    The trace-side half of §4.0.1 — `cap_messages` pinning human turns — has
    been in `tag_trace_sanitize` all along; this is the half that did not
    follow it into the fold (cold review, 2026-08-28).
    """
    return (
        f"{instructions}\n\n"
        f"The position you write must be at most {max_chars} characters.\n\n"
        f"{rendered}\n\n"
        f"Your previous answer was {len(over)} characters, which is over the "
        f"{max_chars}-character limit, so it was NOT saved. Consolidate it now "
        f"and answer again, within the limit. Drop the oldest and least "
        f"consequential thing in it, never the most recent. "
        f"What the room SAID outranks what you concluded about it: if the "
        f"choice is between dropping something someone actually said and "
        f"dropping a judgement of your own, drop your judgement — even when "
        f"yours is the more recent of the two. Recency decides only between "
        f"two things of the same kind.\n\n"
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


def _verdict(
    raw: str, *, tag_id: str, conversation_id: str, now_ms: int,
) -> TurnVerdict | None:
    """The `TurnVerdict` out of the model's JSON, or `None`.

    **Its own validated shape, parsed separately from `standing`** (§7). The
    response schema is deliberately NOT one object with optional fields: a type
    covering both outputs makes every field optional, which means neither shape
    is validated and the sink can refuse nothing. Two shapes, each checked on
    its own terms.

    **A missing or malformed verdict does not cost the position.** They are
    different outputs of one call and the debrief's whole posture is that a
    partial result beats none — so a fold that revised the position but wrote a
    junk verdict still saves the position. That asymmetry is deliberate and is
    why this returns `None` rather than raising: `None` means "no verdict this
    turn", not "the fold failed".

    The construction can still raise — `TurnVerdict.__post_init__` refuses a
    grounded turn carrying unsupported claims, an over-long claim, or too many
    of them — so it is caught here and degraded to `None` rather than being
    allowed to take down a turn that already delivered its reply.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    grounded = parsed.get("grounded")
    if not isinstance(grounded, bool):
        return None
    raw_unsupported = parsed.get("unsupported") or []
    if not isinstance(raw_unsupported, list):
        return None
    claims = tuple(
        c.strip() for c in raw_unsupported
        if isinstance(c, str) and c.strip()
    )
    try:
        return TurnVerdict(
            tag_id=tag_id, conversation_id=conversation_id,
            grounded=grounded, unsupported=claims, created_at=now_ms,
        )
    except ValueError:
        log.warning(
            "debrief: the model's verdict was refused by the domain type "
            "(tag=%s); keeping the position, dropping the verdict", tag_id,
        )
        return None


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
    ceiling underneath it that no config can pass. `window` is CAPPED at
    `[store] context_window` and now equals it: the fold reads
    `ctx.transcript`, the rows the reply path already assembled, so it can
    never see more than she answered with. It WAS deliberately smaller (S3);
    operator override 2026-08-27, because a fold reasoning from 12 rows judges
    a turn it can only see a third of.
    """

    store: StandingStore
    # The per-turn verdict's store (26 §3). OPTIONAL and separate
    # from `store` above, because the two outputs have opposite
    # cardinalities and opposite lifetimes: one row per
    # conversation revised in place, one row per turn never
    # rewritten. `None` is a real state — the verdict is written
    # and forgotten until a re-derivation pass reads it, and that
    # pass does not exist yet, so a deployment may legitimately
    # revise positions without recording verdicts.
    store_verdict: TurnVerdictStore | None
    # The note tier's DreamSink (26 §3's fourth output). `None` on any
    # deployment that has not wired the note store — the debrief then
    # parses no proposals and raises none, the same inert shape
    # `store_verdict` uses.
    notes_sink: DreamSink | None
    # WORKSPACE resolver, not a constant: `propose`'s key is the workspace
    # (§3/DEC-105) and this deployment has two that policy.toml says are
    # never paired, so a fixed string would file one room's notes in the
    # other's notebook. `None` for a room policy cannot name — which is a
    # refusal to propose, never a guessed workspace.
    workspace_of: Callable[[str, str], str | None]
    client: ModelClient
    objective: ObjectiveSource
    max_chars: int
    window: int
    timeout_seconds: float
    store_timeout_seconds: float
    rebuild_after_folds: int


#: How many notes one fold may raise. The objective asks for at most two and
#: says most turns raise none; this is the code half of that, because an
#: objective is a request and a cap is a guarantee. A fold emitting six notes
#: has not found six uncertainties — it has started narrating — and the note
#: index is UNCAPPED by design (a note missing from it is invisible), so the
#: cost of a chatty fold is paid on every subsequent turn's prompt forever.
MAX_NOTES_PER_FOLD = 2


def _note_proposals(
    raw: str, *, conversation_id: str, tag_id: str, notes_read: frozenset[str],
    metrics: Metrics,
) -> list[Any]:
    """The note creations out of the model's JSON — its OWN validated shape.

    **Two shapes with a discriminant, never one with optional fields** (§7). A
    creation needs title/description/uncertainty/closing_condition/anchor; a
    revision needs body and a parent revision id. One type covering both makes
    every field optional, so neither is validated and the sink can refuse
    nothing. The debrief raises CREATIONS only — revising a note it read is the
    tag path's job in-turn (R-55) and the curator's across notes (§2), and
    `NoteRevisionProposal`'s own docstring says the curator is its caller.

    **Refused, never truncated.** `NoteCreationProposal.__post_init__` raises on
    an over-long title rather than trimming, because the tier rests on the index
    being complete and a silent trim loses content. A 61-character title is the
    likely model output, so it is caught HERE and the note dropped — one bad
    note costs that note, not the batch and not the fold.

    **§7's read-set refusal.** A note whose title matches one the turn already
    READ is refused: the tag path may have opened that note and deliberately
    decided against touching it, and the debrief reviewing the same transcript
    would raise it again. Without this the two paths argue once per turn,
    forever. Matched through `note_handle`, the same normalisation R-55's latch
    uses — two spellings of one title must not read as two notes.

    Returns a list, never raises. Every failure is a REFUSED PROPOSAL, not a
    failed fold: the position is what the next turn reads, and a note the model
    got wrong must not cost it.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    entries = parsed.get("notes") or []
    if not isinstance(entries, list):
        return []

    out: list[Any] = []
    for entry in entries[:MAX_NOTES_PER_FOLD]:
        if not isinstance(entry, Mapping):
            metrics.inc("nora_debrief_note_proposals_total", outcome="malformed")
            continue
        fields = _text_fields(entry)
        if fields is None:
            # A non-string where a string belongs is MALFORMED, not something
            # to render. See `_text_fields`.
            metrics.inc("nora_debrief_note_proposals_total", outcome="malformed")
            continue
        title = fields["title"]
        if title and note_handle(title) in notes_read:
            # §7: the turn already read this note and chose not to touch it.
            metrics.inc("nora_debrief_note_proposals_total", outcome="already_read")
            continue
        try:
            out.append(NoteCreationProposal(
                title=title,
                description=fields["description"],
                uncertainty=fields["uncertainty"],
                closing_condition=fields["closing_condition"],
                established_in=conversation_id,
                body=fields["body"],
                anchor_date=_anchor_date(entry.get("anchor_date")),
            ))
        except (ValueError, TypeError) as exc:
            # The domain type refusing is the boundary working, not a fault:
            # an over-long title, a blank closing condition, no anchor.
            log.info(
                "debrief: a proposed note was refused by the domain type "
                "(tag=%s): %s", tag_id, exc,
            )
            metrics.inc("nora_debrief_note_proposals_total", outcome="refused")
    if len(entries) > MAX_NOTES_PER_FOLD:
        metrics.inc(
            "nora_debrief_note_proposals_total", outcome="over_cap",
            value=len(entries) - MAX_NOTES_PER_FOLD,
        )
    return out


#: The note fields that must arrive as TEXT. `anchor_date` is not among them —
#: it has its own parser, which refuses rather than guesses for the same reason.
_TEXT_FIELDS = ("title", "description", "uncertainty", "closing_condition", "body")


def _text_fields(entry: Mapping[str, Any]) -> dict[str, str] | None:
    """The five text fields, or `None` if any of them is not text.

    **`str(...)` was the bug this replaces, and it is the same class of error
    as anchoring an unparseable date to today.** A model emitting
    `"title": ["dinner", "9th"]` produced a note LITERALLY TITLED
    `['dinner', '9th']` — stringifying a list is a guess about what the model
    meant, dressed as parsing. It then went into the notes index, which is
    uncapped and injected into every later prompt, where nothing downstream
    could tell it from a title a person would recognise.

    The objective promises "an entry missing any of them is discarded whole
    rather than guessed at", and the verdict parser one function away refuses a
    non-bool `grounded` outright. This is that same rule, applied where it was
    missing.

    A MISSING field is not the same as a wrong-typed one: absent (or null)
    becomes `""` and is then refused by `NoteCreationProposal.__post_init__`
    with the domain's own message, which is where that judgement belongs. Only
    a value of the wrong SHAPE — a list, a dict, a number — is refused here,
    because there is no honest string to hand on (cold review, 2026-08-28).
    """
    out: dict[str, str] = {}
    for name in _TEXT_FIELDS:
        value = entry.get(name)
        if value is None:
            out[name] = ""
        elif isinstance(value, str):
            out[name] = value.strip()
        else:
            return None
    return out


def _anchor_date(value: object) -> int | None:
    """A YYYYMMDD anchor, or `None` — never a guess.

    `None` reaches `__post_init__`, which refuses a note anchored to nothing, so
    an unparseable date becomes a refused note rather than one anchored to
    today. Anchoring to a default would put a note in the index under a day
    nobody named, and mechanical retirement reads that day.
    """
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _Rebuild(NamedTuple):
    """Why the position is being re-derived, and — for `"ungrounded"` only —
    WHICH verdict caused it.

    `consumed_verdict_tag` exists so the caller can advance the watermark to the
    exact verdict this rebuild acted on, rather than to "the newest" or to
    "now". Those differ: a verdict consumed two turns after it was written must
    be marked by NAME, or a second ungrounded verdict arriving in between is
    swallowed by a watermark claiming more than the rebuild actually read.

    It is the `tag_id` and not the `created_at` because a timestamp merely
    describes a verdict — two can share a millisecond, which both
    `latest_ungrounded` implementations already assert when they justify their
    tie-break.

    `None` on a `"ceiling"` rebuild, and that is load-bearing rather than
    incidental: the ceiling has not looked at any verdict, so it must not
    claim to have consumed one.
    """

    trigger: str
    consumed_verdict_tag: str | None = None


async def _should_rebuild(
    *,
    prior: Standing | None,
    store_verdict: TurnVerdictStore | None,
    conversation_id: str,
    ceiling: int,
    store_timeout_seconds: float,
    tag_id: str,
) -> _Rebuild | None:
    """S2's trigger: `"ungrounded"`, `"ceiling"`, or `None` for neither.

    **`prior is None` is never a re-derivation.** The first fold of a
    conversation builds from the window regardless — there is nothing to carry
    forward and nothing to correct — so counting it would drown the signal in
    the one event that happens to every conversation exactly once.

    **Ungrounded WINS when both hold**, and that ordering is the measurement
    rather than a preference. 26 §6 reads the by-trigger split to ask whether
    the trigger has degraded into "a clock wearing a condition": if `ceiling`
    dominates, it has. Labelling a turn that actually went wrong as `ceiling`
    manufactures exactly that false reading.

    **A verdict store that cannot be read does NOT trigger a rebuild.** An
    unreadable verdict is «I could not check» — R-27 — and re-deriving on it
    would throw away a position because an instrument was down. The ceiling is
    still evaluated, because `fold_count` is on the row already in hand and
    needs no second store.
    """
    if prior is None:
        return None

    if store_verdict is not None:
        try:
            last = await asyncio.wait_for(
                store_verdict.latest_ungrounded(
                    conversation_id=conversation_id),
                timeout=store_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 — an unreadable verdict is R-27's
            # «I could not check», and the ONLY safe answer is not to rebuild:
            # throwing away a position because the instrument was down is a
            # worse outcome than carrying a possibly-drifted one for one more
            # turn. Deliberately blind, because every failure of that read has
            # the same correct response.
            log.warning(
                "debrief: could not read the last verdict (tag=%s); not "
                "re-deriving on an instrument that is down", tag_id,
            )
            last = None
        # **The trigger is a WATERMARK, not a timestamp comparison**, and the
        # guard is on the READ rather than on the write.
        #
        # `latest` answers the newest verdict of ANY AGE, not the previous
        # turn's. A verdict is only written when the fold fully succeeded, so a
        # fold that timed out, overflowed or was screened leaves none — and
        # without this comparison the NEXT fold reads the same stale ungrounded
        # verdict and rebuilds again, and the one after that, forever. Verified
        # live by an independent reviewer: one ungrounded verdict plus three
        # verdict-less folds produced three rebuilds with `fold_count` pinned
        # at 1. Standing silently stopped being a rolling position and became a
        # per-turn window summary — 26 §4.0's recency-slice failure reached
        # from the other side — while
        # `nora_standing_rederivations_total{trigger="ungrounded"}` climbed,
        # which §6 reads as the trigger being HEALTHY. The metric reported the
        # opposite of what was happening.
        #
        # **Why the guard and not "write the verdict on every path".** That was
        # the first proposed fix and it is wrong: a fold that timed out has
        # formed no judgement about groundedness, so writing one would make an
        # instrument that could not measure report a value instead of
        # `unavailable` (R-27). Sparse verdicts are the CORRECT state; the read
        # has to cope with them.
        #
        # **Two comparisons were tried here first and BOTH were wrong**, and
        # the reason they were wrong is the reason this is a column.
        #
        # `created_at > prior.updated_at` — dead on arrival.
        # `pipeline._debrief` stamps ONE `int(time.time() * 1000)` per turn and
        # hands it to `run`, which writes the position with `updated_at=now_ms`
        # and the verdict with `created_at=now_ms`. A fully-successful fold
        # leaves the two EXACTLY EQUAL, deterministically, every turn, so a
        # strict `>` refused the only sequence the guard existed for. The
        # counter read flat zero — which §6 interprets as the trigger having
        # degraded into a clock, the same false reading the original defect
        # manufactured, reached from the other side.
        #
        # `created_at >= prior.updated_at` — closer, still a proxy. It uses the
        # position's own last write to stand in for "a rebuild consumed this
        # verdict", and any proxy is falsifiable by a path that moves it
        # WITHOUT doing the thing. That path is ordinary: `latest` fails for
        # one turn, the trigger correctly declines to fire (an unreadable
        # instrument is R-27's «I could not check», never «there was
        # nothing»), the fold then revises normally and advances `updated_at`
        # past the verdict — and from that turn on the comparison is false
        # FOREVER. The ungrounded verdict is not deferred, it is gone. Driven
        # through the real stage over three turns before this column existed:
        # the verdict was dropped and the counter read zero.
        #
        # So the property is recorded. `consumed_verdict_tag` NAMES the
        # verdict a re-derivation acted on, advanced only when the rebuild is
        # WRITTEN. `!=` rather than an ordering: any verdict this position has
        # not consumed is unconsumed, and no clock is involved.
        #
        # The tag rather than the `created_at` because a third proxy was hiding
        # here too — two ungrounded verdicts in one millisecond collapse to a
        # single watermark value and the second is never acted on (cold review,
        # 2026-08-28).
        # `last` is already ungrounded BY THE READ — see
        # `TurnVerdictStore.latest_ungrounded`. This used to ask `latest` for
        # the newest verdict of either kind and then test `not last.grounded`
        # here, which quietly answers a different question: a grounded verdict
        # from a later turn BURIES an older unconsumed ungrounded one, and the
        # trigger never sees it again. That is not exotic — it is the ordinary
        # shape of a successful turn following one where this read failed.
        if (
            last is not None
            and last.tag_id != prior.consumed_verdict_tag
        ):
            return _Rebuild("ungrounded", last.tag_id)

    if prior.fold_count >= ceiling:
        return _Rebuild("ceiling")
    return None


async def run(
    *,
    trace: TagTrace,
    platform: str,
    store: StandingStore,
    store_verdict: TurnVerdictStore | None,
    notes_sink: DreamSink | None,
    workspace_of: Callable[[str, str], str | None],
    client: ModelClient,
    objective: ObjectiveSource,
    metrics: Metrics,
    display_name: str,
    max_chars: int,
    window: int,
    timeout_seconds: float,
    store_timeout_seconds: float,
    rebuild_after_folds: int,
    now_ms: int,
    notes_read: frozenset[str] = frozenset(),
    tz: tzinfo | None = None,
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

        # S2's TRIGGER. Two conditions, and the ORDER of the check is the
        # measurement: when both hold, this is an `ungrounded` re-derivation,
        # never a `ceiling` one. The ceiling would have fired eventually
        # anyway; an ungrounded verdict names a turn that actually went wrong,
        # and labelling it `ceiling` makes the clock look dominant — which is
        # exactly the reading 26 §6 uses to conclude the trigger has degraded
        # into a clock wearing a condition.
        rebuild = await _should_rebuild(
            prior=prior, store_verdict=store_verdict,
            conversation_id=trace.conversation_id,
            ceiling=rebuild_after_folds,
            store_timeout_seconds=store_timeout_seconds,
            tag_id=trace.tag_id,
        )
        if rebuild is not None:
            # **This counts the trigger FIRING, not the re-derivation
            # landing**, and the two are different questions. A fold that
            # refuses writes no position and advances no watermark, so the
            # trigger fires again next turn — correctly, the verdict genuinely
            # has not been acted on — and on this counter alone a permanently
            # failing fold is indistinguishable from a latched trigger. That
            # ambiguity is what made the original defect readable as health.
            # `nora_standing_rederivations_written_total` below is the other
            # half; the gap between them is the failing-fold rate.
            metrics.inc("nora_standing_rederivations_total",
                        trigger=rebuild.trigger)
            log.info(
                "debrief: re-deriving the position (tag=%s trigger=%s); the "
                "prior is read but not carried forward",
                trace.tag_id, rebuild.trigger,
            )

        # R-55's latch, for 26 §7 — counted here and CONSUMED below, where
        # `_note_proposals` drops any proposal naming a note in this set.
        #
        # The counter predates the consumer and is kept now that it has one:
        # it is the only thing that distinguishes "the turn read nothing" from
        # "the set never arrived", and `_notes_read_of` degrades a failure to
        # an empty set rather than raising, so those two look identical
        # everywhere else.
        #
        # §7 places this refusal AT THE SINK and it is not there: `propose`
        # takes no read set and could not enforce it. Producer-side is the
        # same behaviour for the one producer that exists and is a convention
        # rather than a guarantee for the curator or a second one.
        if notes_read:
            metrics.inc("nora_debrief_notes_read_total", value=len(notes_read))

        instructions = objective.objective_body(OBJECTIVE)
        # **The prior is DISCARDED AT THE RENDER, never at the read** (26 §4's
        # non-negotiable). The fold must still READ it — that is the whole
        # requirement, and `nora_debrief_prior_total` has to stay honest about
        # whether it did — so re-derivation withholds it from the PROMPT rather
        # than skipping the store call. Skipping the read would make a
        # re-derived turn indistinguishable from a fold that could not read,
        # which is the one thing that counter exists to tell apart.
        rendered = render_debrief_input(
            trace=trace, prior=None if rebuild is not None else prior,
            display_name=display_name, window=window, tz=tz,
        )

        # ONE `wait_for` around BOTH calls, not one around each: the budget the
        # slot can afford is a total, and two independently-bounded calls add
        # up to twice it on exactly the turn that was already slow.
        async def _fold() -> tuple[str | None, str, str]:
            """`(body, refusal_label, raw)`.

            `raw` rides along so the VERDICT parses from the same completion
            the position came from — one call, two outputs (§3). On the
            consolidate retry it is the SECOND response, which is correct:
            that is the answer that was actually accepted, and taking the
            verdict off the refused one would pair a position with a judgement
            of a draft nobody kept.
            """
            reply = await _complete(client, call="fold", trace=trace, prompt=_prompt(
                instructions=instructions, rendered=rendered,
                max_chars=max_chars,
            ))
            first = _check(reply, trace.tag_id)
            if first[0] is None or len(first[0]) <= max_chars:
                return first[0], first[1], reply.content
            metrics.inc("nora_standing_overflow_refusals_total", stage="first")
            # Its OWN span, not a second event on the first one. The retry is a
            # different prompt producing a different answer, and a trace that
            # folded them together would show one generation whose input does
            # not explain its output — the shape §4.0's consolidate exists to
            # make legible.
            retry = await _complete(
                client, call="consolidate", trace=trace,
                prompt=_consolidate_prompt(
                    instructions=instructions, rendered=rendered,
                    max_chars=max_chars, over=first[0],
                ),
            )
            second = _check(retry, trace.tag_id)
            return second[0], second[1], retry.content

        body, refusal, raw = await asyncio.wait_for(
            _fold(), timeout=timeout_seconds)
        if body is None:
            outcome = refusal
            return

        # Parsed from the SAME completion the position came from — one call,
        # two outputs (§3) — with its own validated shape and its own failure
        # mode, never a widened schema with optional fields (§7).
        #
        # **BELOW the `body is None` guard, deliberately.** `_check` refuses a
        # `finish_reason == "length"` body BEFORE parsing, precisely because a
        # truncated JSON document can parse into something — a shorter string,
        # a dict missing its last key — and that «something» is an ERROR
        # wearing a PASS. Parsing the verdict ABOVE the guard handed that same
        # truncated text to `json.loads` anyway. It was harmless only because
        # the result was discarded on every refusal path, which made it a
        # loaded gun: the moment a verdict could be written on a path that did
        # not fully succeed, a truncated body parsing to `{"grounded": true}`
        # would become a NEVER-REWRITTEN row asserting a judgement the fold
        # never formed. Below the guard, a refused body cannot reach the parser
        # at all (cold review, 2026-08-28).
        verdict = _verdict(
            raw, tag_id=trace.tag_id, conversation_id=trace.conversation_id,
            now_ms=now_ms,
        )

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
            # Counted from the prior the fold actually read, so the ceiling
            # half of S2's re-derivation trigger counts folds-since-rebuild
            # rather than folds-ever.
            # RESETS to 1 on a rebuild, or the ceiling fires on every turn
            # forever after the first time — a trigger that latches on is a
            # clock with extra steps. Otherwise counted from the prior the
            # fold actually read, so the ceiling counts folds-since-rebuild
            # rather than folds-ever.
            fold_count=1 if rebuild is not None else (
                (prior.fold_count + 1) if prior is not None else 1),
            updated_at=now_ms,
            # S2's WATERMARK, and it advances on exactly one condition: an
            # `ungrounded` rebuild that reaches this write. Carried forward
            # unchanged otherwise — on a revise, on a `ceiling` rebuild (which
            # has read no verdict and must not claim to have consumed one),
            # and on a first fold, where there is nothing to carry.
            #
            # It NAMES the verdict, rather than recording when the rebuild
            # happened. The verdict acted on was written on an earlier turn,
            # so marking "now" would swallow anything that arrived in
            # between — a watermark claiming more than the rebuild read.
            #
            # Never advanced on a refusal path, because this whole expression
            # sits below the guards that return early. That is what makes the
            # retry survive: a rebuild that could not complete leaves the
            # verdict unconsumed and the next turn tries again.
            consumed_verdict_tag=(
                rebuild.consumed_verdict_tag
                if rebuild is not None
                and rebuild.consumed_verdict_tag is not None
                else (prior.consumed_verdict_tag if prior is not None else None)
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
            # reads the new `fold_count` and S2's ceiling counts a fold this
            # log line called unwritten.
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
        if rebuild is not None:
            # The re-derivation LANDED — the position is written and, for an
            # `ungrounded` trigger, the watermark went with it. Paired with
            # `nora_standing_rederivations_total` above, which counts the
            # trigger firing: equal series mean every re-derivation completes,
            # and a persistent gap is a fold that keeps failing rather than a
            # trigger that has latched. Those two look identical on the fired
            # counter alone, and reading one as the other is how the original
            # latch stayed invisible.
            metrics.inc("nora_standing_rederivations_written_total",
                        trigger=rebuild.trigger)

        # THE PER-TURN VERDICT, written AFTER the position and never allowed to
        # cost it. Two outputs of one call (26 §3) with opposite cardinalities:
        # `standing` is one row per conversation revised in place, this is one
        # row per turn NEVER rewritten. The split was found by a bug — per-turn
        # fields on the per-conversation row meant the re-derivation trigger
        # read a `grounded` flag the next turn had already overwritten.
        #
        # ORDER IS DELIBERATE. The position is the output the next turn
        # actually reads; the verdict is read by a re-derivation pass that does
        # not exist yet. So a verdict failure must never undo a position that
        # was written successfully — `outcome` is already "written" above and
        # nothing below changes it. The verdict's own disposition rides on its
        # own counter instead.
        #
        # `store_verdict is None` is the ordinary state on any deployment that
        # has not wired one, and in every offline/eval composition — the same
        # optional-collaborator shape the rest of this stage uses.
        if store_verdict is not None and verdict is not None:
            try:
                await asyncio.wait_for(
                    store_verdict.save(verdict=verdict), timeout=store_timeout_seconds)
                metrics.inc("nora_turn_verdict_total",
                            grounded=str(verdict.grounded).lower())
            except Exception:
                # Including the duplicate-key error that IS the never-rewritten
                # property: a second verdict for one turn means the turn was
                # debriefed twice, which is worth a loud log and a counter and
                # is NOT worth undoing a good position over.
                log.exception(
                    "debrief: the turn verdict did not save (tag=%s); the "
                    "revised position stands", trace.tag_id,
                )
                metrics.inc("nora_turn_verdict_total", grounded="unwritten")
        elif store_verdict is not None:
            # The model revised the position but gave no usable verdict. A
            # reading, not a failure — a sustained rate here means the
            # objective is not asking for one clearly enough.
            metrics.inc("nora_turn_verdict_total", grounded="absent")

        # 26 §3's FOURTH output, and last of the three that ride after the
        # position. Same rule as the verdict, one output over: written AFTER
        # the position, nothing below changes `outcome`, and its disposition
        # rides on its own counter. The position is what the NEXT TURN reads; a
        # note the model got wrong must never cost it.
        #
        # **ONE refusal path, not a did-they-all-land check.** `propose`
        # validates the whole batch before any write and is atomic, and every
        # failure ESCAPES as an exception — TypeError on a foreign payload,
        # StaleNoteHeadError on a lost compare-and-swap, ValueError from the
        # domain types. None is swallowed into a short id list, so there is no
        # partial result to reconcile: either every proposal landed or none did.
        # **EVERYTHING about the proposals happens HERE, below the position
        # write, and the placement is the fix for three findings at once.**
        #
        # It used to sit up beside the verdict parse, above the overflow gate
        # and the screen. Three consequences, none of them intended:
        #
        # 1. `workspace_of` is an INJECTED callable and it ran on the
        #    position's critical path — before the gates, before `store.write`,
        #    outside any `try` of its own, and even when `notes_sink is None`
        #    and no proposal could happen at all. A raise from someone else's
        #    function became `outcome="error"` and THE POSITION WAS NEVER
        #    WRITTEN. This stage says twice that a proposal failure must never
        #    cost the position, and then put a foreign function in front of it
        #    — on every deployment, including the majority that have notes off.
        # 2. `_note_proposals` counts its refusals as it goes, so folds that
        #    were about to be refused for overflow or screened out still
        #    incremented `already_read` and `over_cap`. Those series then
        #    describe proposals that could never have been made.
        # 3. The no-workspace refusal was SILENT — no counter, no log. A
        #    `policy.toml` edit that stops naming a room disables note
        #    proposals with zero signal, and a flat `proposed` series is
        #    indistinguishable from a model that simply had nothing to raise.
        #
        # Below the write, all three go away: the position is already durable,
        # so nothing here can cost it; the counters only describe folds that
        # actually reached this point; and the refusal is named (cold review,
        # 2026-08-28).
        workspace: str | None = None
        proposals: list[NoteCreationProposal] = []
        if notes_sink is not None:
            # **NEVER RAISES**, for the reason above: the resolver belongs to
            # the caller and this sits downstream of a write that has already
            # committed.
            #
            # Three outcomes, three labels, and the split is the point. `None`
            # is a room policy deliberately does not name — a refusal. A raise
            # is a resolver that needs fixing — a fault. Counting both as
            # `no_workspace` would report a broken resolver as a policy
            # decision on the one series an operator reads to tell them apart,
            # which is the R-27 substitution this file argues against
            # everywhere else.
            try:
                workspace = workspace_of(platform, trace.conversation_id)
            except Exception:
                log.exception(
                    "debrief: the workspace resolver failed (tag=%s); no "
                    "proposals this turn, the position is unaffected",
                    trace.tag_id,
                )
                metrics.inc("nora_debrief_note_proposals_total",
                            outcome="no_workspace_unavailable")
                workspace = None
            else:
                if workspace:
                    proposals = _note_proposals(
                        raw, conversation_id=trace.conversation_id,
                        tag_id=trace.tag_id, notes_read=notes_read,
                        metrics=metrics,
                    )
                else:
                    # A refusal to propose, never a guessed notebook: filing
                    # one social unit's notes into another's is not
                    # recoverable by looking at the index. COUNTED, because an
                    # unmapped room is otherwise completely invisible — a
                    # `policy.toml` edit could disable note proposals with no
                    # signal at all.
                    metrics.inc("nora_debrief_note_proposals_total",
                                outcome="no_workspace")

        # DEC-128: the digest of what was ACTUALLY SENT, read per run and never
        # cached — the objective hot-reloads on mtime, so a digest captured at
        # construction would stop naming the text the model saw.
        sent_sha256 = instruction_sha256(instructions)

        if notes_sink is not None and proposals and workspace:
            try:
                written = await asyncio.wait_for(
                    notes_sink.propose(
                        key=workspace, proposals=[
                            Proposal(
                                payload=payload,
                                instruction_sha256=sent_sha256,
                                model=client.model,
                                established_by=trace.tag_id,
                            )
                            for payload in proposals
                        ],
                        now_ms=now_ms,
                    ),
                    timeout=store_timeout_seconds,
                )
                metrics.inc("nora_debrief_note_proposals_total",
                            outcome="proposed", value=len(written))
            except Exception:
                # **Says what it KNOWS.** This used to say the proposals
                # "did not land", which is a claim about store state made
                # without reading it back — and on a `wait_for` timeout the
                # propose is cancelled MID-STATEMENT, so whether the batch
                # committed is not knowable from here. The position write two
                # blocks up was corrected for exactly this once already; the
                # same sentence had been copied down here.
                #
                # The LABEL stays `unwritten`, which is honestly «I could not
                # confirm a write», and the position is genuinely unaffected
                # either way.
                log.exception(
                    "debrief: the note proposals did not complete (tag=%s); "
                    "whether they committed is unknown from here, and the "
                    "revised position stands either way", trace.tag_id,
                )
                metrics.inc("nora_debrief_note_proposals_total",
                            outcome="unwritten", value=len(proposals))
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
        # says so.
        # **NEVER DOWNGRADES A WRITTEN POSITION.** The verdict save and the
        # note propose both run AFTER `outcome = "written"` and both guard only
        # `except Exception`, so a `CancelledError` in either escapes to here —
        # and relabelling that turn `cancelled` would report a position that IS
        # in the database as one that was not revised. `written` is a claim
        # about a committed row; the two riders below it are explicitly not
        # allowed to change it, and neither is a cancellation arriving during
        # one of them (cold review, 2026-08-28).
        if outcome == "written":
            log.info(
                "debrief cancelled after the position was written (tag=%s); "
                "the position stands and a rider did not finish", trace.tag_id,
            )
        else:
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
        #
        # Same rule as the cancel branch above: `outcome` is not touched here
        # at all, so a fault in a rider cannot unsay a written position.
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


def _check(completion: Completion, tag_id: str) -> tuple[str | None, str]:
    """`(body, refusal_label)` — and `finish_reason` is checked FIRST.

    A `"length"` finish means the body is truncated, so it must never reach
    `json.loads`: a truncated JSON document can parse into something (a shorter
    string, a dict missing its last key) and that «something» is an ERROR
    wearing a PASS. Refusing before the parse is what keeps the two apart.

    **The two failures carry DIFFERENT labels and that is the point**, even
    though both write nothing. `"unavailable"` is «the call was cut off, I
    could not check»; `"malformed"` is «the model answered and the answer was
    not the shape asked for». R-27's whole discipline is that those are not the
    same reading: a rising `unavailable` says the output ceiling or the input
    size is wrong, a rising `malformed` says the objective text or the schema
    is. Collapsing them would make the one instrument this stage has unable to
    tell an infrastructure problem from a prompt problem.
    """
    if completion.finish_reason == "length":
        log.warning(
            "debrief: the fold was cut off at the output ceiling (tag=%s); "
            "not parsing a truncated body", tag_id,
        )
        return None, "unavailable"
    body = _body(completion.content)
    return (body, "") if body is not None else (None, "malformed")
