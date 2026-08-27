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

The design's §3 names four outputs of one call. This stage produces **one**:
`Standing`. `TurnVerdict` is Phase 3 and needs its own never-rewritten table;
fact proposals are Phase 4; and the note proposal (plan task 2.10) needs
`SqlNoteStore` to satisfy `DreamSink` — it has `create`/`append_revision`, not
`propose`/`supersede`, and joining it to
`tests/contract/test_dream_sink_contract.py` is another workstream's scope. The
response schema below is deliberately not widened in advance for any of them:
an optional field nothing validates is exactly the failure §7's
two-shapes-with-a-discriminant decision was made to avoid.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import tzinfo
from typing import Protocol

from nora.agent.dream_screen import screen
from nora.domain.ports import StandingStore, TurnVerdictStore
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_verdict import TurnVerdict
from nora.obs.metrics import Metrics

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
    """
    return (
        f"{instructions}\n\n"
        f"The position you write must be at most {max_chars} characters.\n\n"
        f"{rendered}\n\n"
        f"Your previous answer was {len(over)} characters, which is over the "
        f"{max_chars}-character limit, so it was NOT saved. Consolidate it now "
        f"and answer again, within the limit. Drop the oldest and least "
        f"consequential thing in it, never the most recent.\n\n"
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
    client: ModelClient
    objective: ObjectiveSource
    max_chars: int
    window: int
    timeout_seconds: float
    store_timeout_seconds: float
    rebuild_after_folds: int


async def _should_rebuild(
    *,
    prior: Standing | None,
    store_verdict: TurnVerdictStore | None,
    conversation_id: str,
    ceiling: int,
    store_timeout_seconds: float,
    tag_id: str,
) -> str | None:
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
                store_verdict.latest(conversation_id=conversation_id),
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
        if last is not None and not last.grounded:
            return "ungrounded"

    if prior.fold_count >= ceiling:
        return "ceiling"
    return None


async def run(
    *,
    trace: TagTrace,
    platform: str,
    store: StandingStore,
    store_verdict: TurnVerdictStore | None,
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
            metrics.inc("nora_standing_rederivations_total", trigger=rebuild)
            log.info(
                "debrief: re-deriving the position (tag=%s trigger=%s); the "
                "prior is read but not carried forward",
                trace.tag_id, rebuild,
            )

        instructions = objective.objective_body(OBJECTIVE)
        # **The prior is DISCARDED AT THE RENDER, never at the read** (26 §4's
        # non-negotiable). The fold must still READ it — that is the whole
        # requirement, and `nora_debrief_prior_total` has to stay honest about
        # whether it did — so re-derivation withholds it from the PROMPT rather
        # than skipping the store call. Skipping the read would make a
        # re-derived turn indistinguishable from a fold that could not read,
        # which is the one thing that counter exists to tell apart.
        rendered = render_debrief_input(
            trace=trace, prior=None if rebuild else prior,
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
            reply = await client.complete(prompt=_prompt(
                instructions=instructions, rendered=rendered,
                max_chars=max_chars,
            ))
            first = _check(reply, trace.tag_id)
            if first[0] is None or len(first[0]) <= max_chars:
                return first[0], first[1], reply.content
            metrics.inc("nora_standing_overflow_refusals_total", stage="first")
            retry = await client.complete(prompt=_consolidate_prompt(
                instructions=instructions, rendered=rendered,
                max_chars=max_chars, over=first[0],
            ))
            second = _check(retry, trace.tag_id)
            return second[0], second[1], retry.content

        body, refusal, raw = await asyncio.wait_for(
            _fold(), timeout=timeout_seconds)
        # Parsed from the SAME completion the position came from — one call,
        # two outputs (§3) — but with its own validated shape and its own
        # failure mode, never a widened schema with optional fields (§7).
        verdict = _verdict(
            raw, tag_id=trace.tag_id, conversation_id=trace.conversation_id,
            now_ms=now_ms,
        )
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
            # Counted from the prior the fold actually read, so the ceiling
            # half of S2's re-derivation trigger counts folds-since-rebuild
            # rather than folds-ever.
            # RESETS to 1 on a rebuild, or the ceiling fires on every turn
            # forever after the first time — a trigger that latches on is a
            # clock with extra steps. Otherwise counted from the prior the
            # fold actually read, so the ceiling counts folds-since-rebuild
            # rather than folds-ever.
            fold_count=1 if rebuild else (
                (prior.fold_count + 1) if prior is not None else 1),
            updated_at=now_ms,
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
