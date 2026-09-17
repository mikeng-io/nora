"""What the debrief is actually SHOWN — the rendering contract from
`design/dreamer-agent-loop/26-standing.md` §4.2, in its own module because the
gap between "the type carries everything" and "something renders it" is how the
fact tier failed: identity present on the row, dropped at the prompt, noticed
months later (`21-substrate-contract.md` §7.1).

**Seven mappings, each one a defect another tier has already shipped.** They are
tested individually rather than through the whole block, because a render that
gets six right and drops the seventh still looks correct:

- **`ts`** — local wall-clock time. Prevents the model reasoning about
  `1756012345`; *"you are not even including the timestamp"* is the fact
  tier's critique verbatim.
- **`is_nora`** — always rendered, as a distinct speaker. Prevents the fact
  tier's exact defect: a batch where nobody can tell who said what.
- **`person_name`** — the DISPLAY name, never `person_id`. §4.3, and this one
  is a leak rather than a legibility bug; see below.
- **`quoted_ts`** — rendered as a reply relation. Without it *"yeah that one"*
  is unresolvable and the debrief mis-reads what the turn was about.
- **`outcome`** — always, including a silence. §2 makes silence the case most
  worth reviewing; a render that omits it defeats the stage's own purpose.
- **`prior`** — always present, **even when empty**. §4's non-negotiable: a
  fold that cannot see the prior position cannot correct it.
- **`delivery_status`** — on Nora's OWN rows, as `(delivered)` /
  `(not delivered)`, with a legend above the transcript saying what the mark
  means. **Issue #199 is the defect this one exists for.** A tag turn died with
  `llm_error`, the header said *"the model call failed; nothing was sent"*, and
  the fold generalised a TURN outcome into a ROOM position — *"I have sent
  nothing at all today"* — with her own delivered answer sitting two rows above
  the tag in the very window it was reading. The row was present and carried
  nothing that said it had reached anybody, so the header won. This is
  `AGENTS.md`'s rule in its rarest form: the input DID carry the truth, and a
  block that did not say what it was scoped to overrode it.

**Wall-clock, not `prefill._age`'s relative form, and the difference is not
cosmetic.** The prefill renders "2h ago" because the question a reply needs
answered is *is this still current?*. The debrief's question is different: it is
reviewing a finished exchange against a position that may be days old, so it
needs to place the turn on a clock — and an age rendered from a clock the fold
reads at a different instant is a second source of truth about when the turn
happened.

**`person_id` never appears here** (§4.3, and this is the rule this module
exists to hold). `Standing` is injected into every subsequent turn for as long
as the row lives, so anything reaching the fold's prompt can persist **by
design** rather than by attack. R-18/R-28 screen egress from a REPLY; Standing
is an input. The identifier is available for the code-side join and has no
reason to be in a prompt whose output persists indefinitely — so the label is
`person_name`/`sender_name`, exactly as `prefill._label` resolves it, and the
id is not carried at all.

Everything group-authored is `escape`d, R-13: the transcript rendered here is
the same group-written text the prefill escapes, one hop further along.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, tzinfo
from typing import Any, Literal, get_args

from nora.domain.blocks import ContextBlock, render_elements, speaker_name
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.prompt import escape
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.obs.metrics import Metrics

#: The scope clause every «sent nothing» gloss carries. ONE constant, spliced
#: into all four of them, so the four cannot drift into saying four different
#: things about the same boundary — and so reverting it is one edit a test can
#: be pointed at.
_THIS_TURN_ONLY = (
    ". That is a fact about THIS TURN and nothing else — it does not describe "
    "what any other turn of yours sent."
)

#: What each `outcome` MEANS, spelled out rather than left as a bare token.
#: §2's whole argument is that a deliberate silence produced just as much
#: reasoning as a reply and is the case most worth reviewing — a fold shown
#: `outcome="silent"` and nothing else has to guess whether that was a choice,
#: a veto or a crash, and those are three different reviews.
# KNOWN GAP, recorded rather than papered over (review, 2026-08-27). A turn
# where the never-silent fallback delivered `_EXHAUSTED_FALLBACK` is labelled
# `send_reply` by `EngagementPipeline` — `recovered_delivered` is only set on
# the DEC-93 prose-recovery branch, not on the fallback one — so the gloss
# below tells the fold "you replied, through the send_reply tool" while the
# rendered `<reasoning>` shows no such call. A self-contradictory input.
#
# NOT fixed here, deliberately. The honest fix is a SEVENTH outcome, and
# `VALID_OUTCOMES` is a closed set backed by `tag_trace_outcome_check` — so it
# needs a migration against a live table, which is Phase 1's territory and not
# something a Phase 2 fix branch should carry (the same reasoning that kept the
# engine-wide `command_timeout` out of this branch).
#
# Currently unreachable in practice: `nora_loop_exhausted_fallbacks_total` has
# never fired in production, and all 29 retained traces are `recovered` (15) or
# `send_reply` (14). Latent, not active — which is why it is a note and not a
# migration.
#
#: **Every gloss is scoped to THIS TURN, and issue #199 is why the scope is
#: spelled out rather than implied.** `llm_error` read "the model call failed;
#: nothing was sent" — true of the turn, silent about its scope — and the fold
#: read it as a statement about the conversation: it wrote «I have not sent a
#: single reply today» while a delivered answer of hers sat in the same
#: transcript window, then proposed a note on the same false premise and the
#: verdict blessed it. The four outcomes that sent nothing now say WHOSE
#: nothing it is, in the same sentence, because a scope the reader has to infer
#: is a scope the reader gets to choose.
_OUTCOME_GLOSS: Mapping[str, str] = {
    "send_reply": "you replied, through the send_reply tool",
    "recovered": "you wrote a reply as prose without calling send_reply; "
                 "the pipeline delivered it for you",
    "silent": "you chose to say nothing on this turn — no delivery was "
              "attempted for it (R-5)" + _THIS_TURN_ONLY,
    "vetoed": "on this turn you tried to reply and every attempt was refused "
              "by a guard" + _THIS_TURN_ONLY,
    "timeout": "this turn's model call did not finish in time, so THIS TURN "
               "sent nothing" + _THIS_TURN_ONLY,
    "llm_error": "this turn's model call failed, so THIS TURN sent nothing"
                 + _THIS_TURN_ONLY,
}

#: How much of one narration/tool-result block reaches the fold. The trace is
#: already capped on the way into retention (`cap_messages`), so this is the
#: SECOND bound and it exists for a different reason: retention's budget is
#: about what is worth keeping, this one is about what fits beside a transcript
#: and a prior position inside one bounded completion. Generous enough that a
#: tool result stays readable, tight enough that one runaway result cannot
#: crowd out the exchange it belongs to.
_BLOCK_CHARS = 600

#: WHICH block `_clip` cut — issue #189 step 1. Grounded on `_block`'s own four
#: branches rather than copied from the issue's prose: `text` and
#: `reasoningContent` are both the MODEL'S OWN output (what it said, what it
#: thought) and share the `reasoning` label; `toolUse` is the model's own
#: request to a tool and gets its own label because a clipped CALL and a
#: clipped RESULT are different failures for a grounding judge — one hides
#: what she asked, the other hides what she was told. `transcript` — the third
#: word in the issue's own list — is deliberately NOT a value here: nothing in
#: this module clips a `TranscriptRow.body` (`_transcript` renders it whole),
#: so a `transcript` label would be a declared value with no call site ever
#: incrementing it — the CMS rule this whole issue is about, one level down.
ClipKind = Literal["reasoning", "tool_call", "tool_result"]
CLIP_KINDS: tuple[str, ...] = get_args(ClipKind)

#: The metric issue #189 step 1 asks for: one clip, one increment, labelled by
#: `ClipKind`. Declared in `nora.obs.metric_specs` (append-only names); this is
#: the name the two modules must agree on.
_CLIPS_METRIC = "nora_debrief_clips_total"

#: The mark a clipped block carries, and the legend that explains it — issue
#: #189 step 2. `_TRANSCRIPT_LEGEND` below is the precedent this follows:
#: **telling the model what it is READING beat telling it what it can do**,
#: measured on the Relevance Gate (#78's closing comment). A block cut at
#: `_BLOCK_CHARS` and handed to the grounding verdict with no mark reads as a
#: complete account that simply doesn't support the claim; the grader cannot
#: tell "the support isn't there" from "I was shown a fragment" unless the
#: fragment says so itself.
_CLIP_MARK = "clipped"


#: The mark on one of Nora's OWN rows, and the LEGEND that says what it means.
#: Issue #199: `is_nora` was rendered as a distinct speaker and stopped there,
#: so a row of hers that had actually reached the room looked exactly like one
#: that had not — and a turn-scoped «nothing was sent» header was free to
#: explain it away. The mark binds to `messages.delivery_status`, the column the
#: sender writes back after the platform accepts the message, so it is the
#: record's own answer to «did this reach anybody» rather than an inference from
#: the turn.
_DELIVERED = "delivered"
_NOT_DELIVERED = "not delivered"


def _delivery(row: TranscriptRow) -> str:
    """The delivery mark for one of Nora's rows — the seventh mapping.

    Only her own rows carry it: `delivery_status` on an inbound row describes a
    send that never happened, so marking those would put a word on a line that
    means nothing there and teach the fold to read the mark as decoration.

    `PENDING` renders as `not delivered` alongside `FAILED`, deliberately. The
    honest reading of a row still pending is «not known to have reached the
    room», and the fail-closed direction on a mark whose whole job is to stop a
    false «I sent nothing» is to under-claim delivery, never to over-claim it.
    """
    return _DELIVERED if row.delivery_status is DeliveryStatus.SENT else _NOT_DELIVERED


def _clock(ts_ms: int, tz: tzinfo | None) -> str:
    """`ts` as a local wall-clock time — the first of the seven mappings.

    `tz=None` means the host's own zone, matching every other clock in the
    prompt (`prefill._now_line`); it is a parameter so a test can pin one and
    so a deployment in another zone renders its own.
    """
    return datetime.fromtimestamp(ts_ms / 1000, tz=tz).strftime("%H:%M")


def _speaker(row: TranscriptRow, display_name: str) -> str:
    """Who said it — `is_nora` as a distinct speaker, and NEVER `person_id`.

    Deliberately the same precedence `nora.agent.prefill._label` uses
    (`person_name`, then the self-set profile name, then "unknown"), because
    the debrief reads a transcript the reply path already rendered under those
    names, and two renderers calling one person two things is how a fold
    concludes two people were talking.
    """
    if row.is_nora:
        return display_name
    # THROUGH THE HELPER — see `prefill._label`. The guard belongs after the
    # collapse, not on the raw value.
    return speaker_name(row.person_name, row.sender_name)


#: A wall CLOCK rather than an age: the fold is read against a calendar («what
#: happened at 11:00») where the reply prompt is read against now. That was
#: `RowStyle(time="clock")` on a flat line until #292 and is now the `at`
#: attribute — same fact, a name instead of a bracket position.


def _transcript(
    rows: Sequence[TranscriptRow], *, display_name: str, tz: tzinfo | None
) -> str:
    by_ts = {row.ts: row for row in rows}

    def _replying(row: TranscriptRow) -> str:
        """The reply relation, by the parent's own rendered CLOCK rather than by
        its id — the fold has the clock on every line above, and has no id for
        anything. An out-of-window or self-referential pointer renders nothing
        at all (`prefill._parent_of`'s rule: a symbol that can mean "unknown"
        invites the model to emit one). Already-rendered text, so it is appended
        after the speaker's escape rather than through it."""
        if row.quoted_ts is None:
            return ""
        parent = by_ts.get(row.quoted_ts)
        if parent is None or parent.id == row.id:
            return ""
        return f"(replying to {_clock(parent.ts, tz)})"

    def _mark(row: TranscriptRow) -> str:
        """THE DELIVERY MARK (issue #199). Hers only, and never escaped from the
        row's own text — it is the render's word about the record, not anybody's
        content."""
        return f"({_delivery(row)})" if row.is_nora else ""

    def _attrs(
        row: TranscriptRow, _i: int
    ) -> tuple[tuple[str, str | None], ...]:
        parent = by_ts.get(row.quoted_ts) if row.quoted_ts is not None else None
        return (
            ("at", _clock(row.ts, tz) if row.ts else None),
            ("from", _speaker(row, display_name)),
            # An out-of-window or self-referential pointer renders NOTHING
            # (`prefill._parent_of`'s rule: a symbol that can mean "unknown"
            # invites the model to emit one).
            ("replying_to", None if parent is None or parent.id == row.id
             else _clock(parent.ts, tz)),
            # THE DELIVERY MARK (issue #199), hers only. The render's word
            # about the record, never anybody's content — which is why it is an
            # attribute rather than something appended to the speaker field.
            ("delivery", _delivery(row) if row.is_nora else None),
        )

    return render_elements(rows, tag="m", attrs=_attrs, body=lambda row: row.body)


def _transcript_block(
    rows: Sequence[TranscriptRow], *, display_name: str, tz: tzinfo | None,
    name: str = "transcript",
) -> str:
    """The turn's or the stretch's messages — one function, so the delivery
    mark and the sentence explaining it can never ship apart.

    **`name` exists because ONE description was false in two of five callers**
    (audit, 2026-09-17). `<transcript>` says «the messages of THIS TURN», which
    is right for the fold, the verdict and the turn render — and wrong for the
    two rollup renders, whose own docstring says «there IS no turn: the event
    fold runs after an untagged stretch». Their wrapper called the same
    messages «the batch» one line above. Two names for one thing, in one
    document, adjacent.

    Worse, `delivery`'s clause reconciles the mark against «a turn's outcome»,
    and 362 characters of that landed in an 890-character rollup-verdict
    document that has no turn and no outcome — 41% of the prompt explaining a
    reconciliation it cannot contain.

    So `<stretch>` is its own type. Same rows, same renderer, same mark; a
    description that is true of what it wraps. This is the `<batch>`/`<now>`
    split a third time, and the third time it was found by reading the RENDER
    rather than the registry — the registry sees names, and one name is exactly
    what hid it.

    Every renderer in this module goes through here. A delivery mark in the
    tag fold's transcript and a bare one in the rollup's would be the same
    token meaning two things to the same model, which is the drift the
    `_speaker` docstring one function up already names for names.

    **The legend is a SIBLING of `<transcript>`, never a line inside it**, and
    that is not cosmetic. Everything between those two tags is a message
    somebody sent; a sentence of ours in there is a line the model — and
    anything else that splits the block on newlines — has to work out is not
    one. Putting an explanation where the evidence goes is a smaller version of
    the defect this whole module is being changed for.
    """
    # THE LEGEND IS GONE, and its content is the block's own `fields=` (#314).
    # It was a block whose entire body was prose describing a SIBLING's fields,
    # which existed only because a field's meaning had nowhere to live. It now
    # travels with the type in `nora.domain.block_types`, so the thing that
    # renders `delivery` is the thing that says what `delivery` means — and a
    # renderer adding a field cannot ship it unexplained.
    #
    # That also retires the reason the two were separate blocks: the legend was
    # OURS and the transcript the room's, so they had to carry opposite markers.
    # There are no markers now, and the explanation is an attribute of the block
    # it explains rather than a sibling of it.
    return ContextBlock(
        name=name,
        body=_transcript(rows, display_name=display_name, tz=tz),
    ).render()


#: **There is no indent helper, and there was one for about ten minutes.** The
#: fold's nested blocks used to be hand-written two spaces in, so the first cut
#: of this conversion added `_indent` to keep the bytes where they were. It
#: indented EVERY line of the rendered block — including the lines inside a
#: member's multi-line message, which silently rewrote their text. Caught by
#: `test_a_multi_line_message_keeps_its_own_speaker_in_the_fold`, which is the
#: test that exists because a body spanning lines is the whole point of the
#: element form. Indentation cannot be applied to a rendered block at all once
#: bodies carry newlines: the block's own lines and the content's lines are the
#: same lines to any string operation. So the nesting is flat now.


def _standing_prior(body: str) -> str:
    """Her previous position, or the sentence saying there is none.

    This is HER fold from a previous turn, and the type says so — «yours to
    continue, not a record of what anybody said». The absent-position sentence
    is this module's own words about that absence.
    """
    return ContextBlock(
        name="standing_prior", body=escape(body) if body else (
            "(no position yet — this is the first fold for this conversation)"
        ),
    ).render()


def _conversation(block: str) -> str:
    """The event-path fold's envelope around an already-rendered transcript."""
    return ContextBlock(
        name="conversation", body=block,
    ).render()


def _clip(text: str, *, kind: ClipKind, metrics: Metrics) -> str:
    """Cut at `_BLOCK_CHARS`, COUNTED and MARKED — issue #189 steps 1 and 2.

    The increment fires only on an actual cut, never on every call: a series
    that moved on every block, clipped or not, would say nothing about how
    often the ceiling is hit, which is the number step 3 needs. The mark is
    appended to the TEXT itself, ahead of `escape()` at every call site, so it
    travels with the block through whichever prefix wraps it
    (`(thinking) …`, `(tool result: …)`) and reaches the model as part of what
    it is reading, not as a side channel it has no reason to trust more than
    the content.
    """
    text = text.strip()
    if len(text) <= _BLOCK_CHARS:
        return text
    metrics.inc(_CLIPS_METRIC, block=kind)
    return f"{text[:_BLOCK_CHARS]} …[{_CLIP_MARK}]"


def _reasoning(messages: Sequence[Mapping[str, Any]], *, metrics: Metrics) -> str:
    """The model exchange, flattened to lines the fold can read.

    **Defensive throughout, for `tag_trace_extract`'s reason**: the blocks are
    Strands' own shapes and a renamed field must degrade to a thinner render,
    never raise into a stage that runs after a turn already succeeded. Every
    lookup below is a `.get` on something checked to be a mapping first.

    Tool calls and results are rendered as such rather than as prose. A fold
    shown `«I'll check where he is»` and then a paragraph cannot tell whether a
    tool ran, and «did the tool actually run» is exactly what separates a
    grounded turn from a plausible one.
    """
    rows: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "?")
        content = message.get("content")
        if not isinstance(content, Sequence) or isinstance(content, str | bytes):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            rendered = _block(block, metrics=metrics)
            if rendered:
                rows.append((role, rendered))
    # ONE STEP, ONE ELEMENT — and this block is NOT hers, whatever its name
    # suggests. A `toolResult` from `load_transcript` or `who_said` is a
    # member's own body and their self-set profile name, so the flat
    # `role: rendered` joined with newlines let a quoted transcript line forge
    # `assistant: (called send_reply with …)` and `user: (tool result: …)` —
    # byte-identical to real steps, in the block the fold reads to decide what
    # happened, which then WRITES Standing. R-13c inside the one place the
    # conversion had framed as her own record.
    return render_elements(
        rows, tag="step",
        attrs=lambda r, _i: (("role", r[0]),),
        body=lambda r: r[1],
    )


def _block(block: Mapping[str, Any], *, metrics: Metrics) -> str:
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return escape(_clip(text, kind="reasoning", metrics=metrics))
    reasoning = block.get("reasoningContent")
    if isinstance(reasoning, Mapping):
        inner = reasoning.get("reasoningText")
        raw = inner.get("text") if isinstance(inner, Mapping) else None
        if isinstance(raw, str) and raw.strip():
            return f"(thinking) {escape(_clip(raw, kind='reasoning', metrics=metrics))}"
    use = block.get("toolUse")
    if isinstance(use, Mapping):
        name = escape(str(use.get("name") or "?"))
        clipped = _clip(str(use.get("input")), kind="tool_call", metrics=metrics)
        return f"(called {name} with {escape(clipped)})"
    result = block.get("toolResult")
    if isinstance(result, Mapping):
        clipped = _clip(str(result.get("content")), kind="tool_result", metrics=metrics)
        return f"(tool result: {escape(clipped)})"
    return ""


def _fold_rows(
    trace: TagTrace, since_rows: Sequence[TranscriptRow],
) -> tuple[TranscriptRow, ...]:
    """The transcript the tag-path fold actually sees: the watermark batch
    (`since_rows`, read via `RecordStore.since_including_nora`) PLUS the
    turn's own rows (`trace.transcript`), deduped by id and ascending.

    The event check fires BEFORE the tag reply (design/standing-watermark/10),
    so an event fold may have already advanced the watermark past the turn's
    rows before this fold runs — `since_rows` would then start AFTER the turn,
    and the fold would review a turn it cannot see. The turn's own rows are
    therefore always included, even when the batch already covers them (D8).
    """
    by_id = {row.id: row for row in since_rows}
    by_id.update({row.id: row for row in trace.transcript})
    return tuple(sorted(by_id.values(), key=lambda r: r.id))


def render_debrief_turn(
    *, trace: TagTrace, display_name: str, metrics: Metrics,
    since_rows: Sequence[TranscriptRow] = (), tz: tzinfo | None = None,
) -> str:
    """The whole turn — outcome, transcript, reasoning — as one block.

    `since_rows` is the watermark batch: every row since the last fold's
    `last_folded_row_id`, read via `RecordStore.since_including_nora` and
    capped at `[standing] fold_batch` (design/standing-watermark/20). The
    transcript is that batch PLUS the turn's own rows (`trace.transcript`),
    deduped by id — see `_fold_rows` for why the turn's rows must always be
    present.

    **The block both calls read, and the standing call extends.** Issue #78
    Phase 2 splits the fold into a position call and a verdict call; the judge
    renders EXACTLY this block, the position call prepends
    `<standing_prior>` to it (`render_debrief_input`). Giving the judge the
    prior would be prompt pollution in the other direction: a judge shown the
    position it is about to write judges the position, not the turn.

    `metrics` is required, not defaulted — issue #189: a render this module
    cannot count is exactly the defect the issue is about, one level up from
    the model it feeds. Both `render_debrief_input` and `render_verdict_input`
    call through here, so a clip in the SAME turn is counted once per call
    that actually renders it — two separate model inputs, each independently
    truncated, not one event double-counted.

    What the clip mark MEANS is the `<reasoning>` type's `clipped` annotation,
    so it is on the block that can carry the mark and is present whether or not
    this turn clipped anything — the model reads the same contract every turn
    rather than learning a mark whose explanation appears only sometimes.

    It was a sibling `<how_to_read_reasoning>` block until #314: a block whose
    entire content was prose about the block beside it, kept next to it by an
    `order` number, which existed only because a field's meaning had nowhere to
    live. Travelling WITH the block is what a sibling could never do.
    """
    rows = _fold_rows(trace, since_rows)
    gloss = _OUTCOME_GLOSS.get(trace.outcome, trace.outcome)
    at = datetime.fromtimestamp(trace.created_at / 1000, tz=tz).strftime(
        "%Y-%m-%d %H:%M %Z"
    ).strip()
    inner = "\n".join((
        (ContextBlock(
            name="what_happened", body=escape(gloss),
        ).render()),
        _transcript_block(rows, display_name=display_name, tz=tz),
        (ContextBlock(
            name="reasoning",
            # MARKED, because a tool result is the room's words. `load_transcript`
            # and `who_said` hand back member-authored bodies and self-set
            # profile names, so this block carries what people typed even though
            # the steps around it are hers. `tests/test_prefill.py`'s own
            # classification names "a tool result" in the marked class; this
            # block was the exception to a rule the tree states in writing.
            body=_reasoning(trace.messages, metrics=metrics),
        ).render()),
    ))
    return ContextBlock(
        name="turn", attrs=(("outcome", trace.outcome), ("at", at)),
        # The ENVELOPE is this process's own record of a turn. The
        # member-authored text inside it is `<transcript>`, which carries its
        # own marker.
        body=inner,
    ).render()


def render_verdict_input(
    *, trace: TagTrace, display_name: str, metrics: Metrics,
    since_rows: Sequence[TranscriptRow] = (), tz: tzinfo | None = None,
) -> str:
    """What the VERDICT call sees: the whole turn, WITHOUT the prior.

    Same transcript, same reasoning, same outcome labelling as the standing
    render — the judge sequences the SAME turn, so a different render here
    would let the two calls disagree about what even happened. Only the prior
    is missing, and that is the point (issue #78 Phase 2): the verdict must not
    read the position it is about to write. `since_rows` is the SAME watermark
    batch the standing call reads (D4: the two calls must not disagree about
    what happened).

    This is the render issue #189 is actually about: the grounding verdict is
    the consumer that judges "was the reply supported by what she saw", and a
    clip here is a clip in exactly the input that call grades.
    """
    return render_debrief_turn(
        trace=trace, display_name=display_name, metrics=metrics,
        since_rows=since_rows, tz=tz,
    )


def render_debrief_input(
    *,
    trace: TagTrace,
    prior: Standing | None,
    display_name: str,
    metrics: Metrics,
    since_rows: Sequence[TranscriptRow] = (),
    tz: tzinfo | None = None,
) -> str:
    """The whole of what the STANDING call sees — the prior plus the turn.

    `<standing_prior>` is emitted **even when there is none**, carrying an
    explicit "no position yet" rather than being omitted. §4.2 fixes that: an
    absent block and a block the fold failed to read look identical from inside
    the prompt, and the one thing this tier cannot afford is a fold that writes
    a second position beside the first because it never saw the first.
    """
    body = prior.body.strip() if prior is not None and prior.body.strip() else ""
    prior_block = _standing_prior(body)
    turn = render_debrief_turn(
        trace=trace, display_name=display_name, metrics=metrics,
        since_rows=since_rows, tz=tz,
    )
    return f"{prior_block}\n\n{turn}"


def render_rollup_input(
    *,
    prior: Standing | None,
    rows: Sequence[TranscriptRow],
    display_name: str,
    tz: tzinfo | None = None,
) -> str:
    """What the EVENT-path fold (the rollup) sees: the prior plus the batch of
    rows since the last fold's watermark (design/standing-watermark/30).

    No `<turn>` block — there IS no turn: the event fold runs after an
    untagged stretch, so the transcript IS the batch and there is no outcome,
    reasoning or tag to label it with. The prior block carries the same
    always-present contract `render_debrief_input` uses — a rollup fold that
    cannot see the prior position cannot correct it.
    """
    body = prior.body.strip() if prior is not None and prior.body.strip() else ""
    prior_block = (
        _standing_prior(body)
    )
    block = _transcript_block(
        rows, display_name=display_name, tz=tz, name="stretch",
    )
    return (
        f"{prior_block}\n\n" + _conversation(block)
    )


def render_rollup_verdict_input(
    *, rows: Sequence[TranscriptRow], display_name: str, tz: tzinfo | None = None,
) -> str:
    """What the event-path fold's VERDICT call sees: the SAME batch WITHOUT the
    prior (D4 — the verdict must not read the position it is about to write,
    and the two calls must not disagree about what happened).
    """
    block = _transcript_block(
        rows, display_name=display_name, tz=tz, name="stretch",
    )
    return _conversation(block)
