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

**3. It holds the slot, so it is bounded twice.** Its own `asyncio.wait_for`,
and `[standing] timeout_seconds` chosen so `tag_timeout + debrief_timeout` fits
inside an acceptable hold. The cost is user-visible — **but not to the person
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
from nora.domain.ports import StandingStore
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
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
    ceiling underneath it that no config can pass. `window` is the debrief's
    OWN transcript window (S3), deliberately smaller than `[store]
    context_window`: the debrief revises a position and does not need the full
    reply context, and inheriting the reply path's number would be
    `05-transport.md`'s inheritance error in another seam.
    """

    store: StandingStore
    client: ModelClient
    objective: ObjectiveSource
    max_chars: int
    window: int
    timeout_seconds: float


async def run(
    *,
    trace: TagTrace,
    platform: str,
    store: StandingStore,
    client: ModelClient,
    objective: ObjectiveSource,
    metrics: Metrics,
    display_name: str,
    max_chars: int,
    window: int,
    timeout_seconds: float,
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
            prior: Standing | None = await store.read(
                platform=platform, conversation_id=trace.conversation_id,
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

        instructions = objective.objective_body(OBJECTIVE)
        rendered = render_debrief_input(
            trace=trace, prior=prior, display_name=display_name,
            window=window, tz=tz,
        )

        # ONE `wait_for` around BOTH calls, not one around each: the budget the
        # slot can afford is a total, and two independently-bounded calls add
        # up to twice it on exactly the turn that was already slow.
        async def _fold() -> tuple[str | None, str]:
            first = _check(
                await client.complete(prompt=_prompt(
                    instructions=instructions, rendered=rendered,
                    max_chars=max_chars,
                )),
                trace.tag_id,
            )
            if first[0] is None or len(first[0]) <= max_chars:
                return first
            metrics.inc("nora_standing_overflow_refusals_total", stage="first")
            return _check(
                await client.complete(prompt=_consolidate_prompt(
                    instructions=instructions, rendered=rendered,
                    max_chars=max_chars, over=first[0],
                )),
                trace.tag_id,
            )

        body, refusal = await asyncio.wait_for(_fold(), timeout=timeout_seconds)
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

        await store.write(standing=Standing(
            platform=platform,
            conversation_id=trace.conversation_id,
            body=body,
            # Counted from the prior the fold actually read, so the ceiling
            # half of S2's re-derivation trigger counts folds-since-rebuild
            # rather than folds-ever.
            fold_count=(prior.fold_count + 1) if prior is not None else 1,
            updated_at=now_ms,
        ))
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
    except BaseException:
        # `BaseException`, and it is not defensive breadth — see the module
        # docstring's (1). `CancelledError` is not an `Exception`, and letting
        # one out of here reports a silent failure on a turn that delivered.
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
