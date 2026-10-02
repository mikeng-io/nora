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

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal, get_args
from urllib.parse import unquote

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    restamp,
    speaker_name,
)
from nora.domain.culture import CultureRevision
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.prompt import age, escape
from nora.domain.quote_window import first_by_ts
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.tag_trace import TagTrace
from nora.language.culture_render import render_culture
from nora.obs.metrics import Metrics

#: What this renderer carries, declared as data
#: (`design/context-assembly/01-the-registry.md`) instead of left as the
#: f-strings and `"\n".join`s that wrote `render_debrief_input` and
#: `render_debrief_turn`.
#:
#: **A table cannot say «nested».** The fold's input is `culture`,
#: `standing_prior`, `standing_working_set_prior`, then ONE `turn`. Those are
#: the blocks `assemble()` walks
#: at the top, joined by a blank line, so their `order` IS the order the model
#: reads (the numbers are the order the f-strings wrote them, step 2b of
#: `design/context-assembly/04-every-occasion.md`; the goldens in
#: `tests/unit/test_step2b_assembly_goldens.py` pin it byte for byte).
#:
#: The rest are text INSIDE one of those, never in the list `assemble` sorts
#: at the top: `what_happened`, `transcript` and `steps` are `turn`'s body,
#: walked by a second `assemble` (one newline apart). Their `order` records the sequence a
#: reader meets them reading INTO the block.
#:
#: **`key="turn"` throughout, and `when_empty="render"` on every block.** The
#: debrief's own contract is that a block is ALWAYS rendered, empty or not
#: (`_standing_prior`: an absent block and one the fold failed to read look
#: identical from inside the prompt), and the hand join kept a window with no
#: rows, a turn with no steps, or a body that escapes to nothing as an empty
#: pair. `assemble` would drop them, which this step is not allowed to do.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("culture", 10, "turn"),
    ("standing_prior", 20, "turn"),
    ("standing_working_set_prior", 30, "turn"),
    ("turn", 40, "turn"),
    ("what_happened", 50, "turn"),
    ("transcript", 60, "turn"),
    ("steps", 70, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


def _stamp(block: ContextBlock) -> ContextBlock:
    """A block at its row in `_BLOCKS`, and kept when its body is empty."""
    return replace(restamp(block, _ROW), when_empty="render")


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
# rendered `<steps>` shows no such call. A self-contradictory input.
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

#: How much of one step reaches the fold, by what the step CARRIES. The trace
#: is already capped on the way into retention (`cap_messages`), so this is the
#: SECOND bound and it exists for a different reason: retention's budget is
#: about what is worth keeping, this one is about what fits beside a transcript
#: and a prior position inside one bounded completion.
#:
#: **Two bounds, not one** (design/standing-compact/10 §3). A tool result and a
#: step of private reasoning are the only content in the turn that exists
#: NOWHERE ELSE: the transcript does not hold what a search returned, and once
#: the turn ends the fold is the last reader it will ever have. At one flat 600
#: the fold was shown a sixteenth of a median `web_search` (9.6k), so a search
#: that found eight restaurants and sent two left no trace of the other six.
#: 4000 carries `web_fetch` and `ask_memory` whole at p90 (measured on 1000
#: production tool calls, 2026-09-09..24). Her own narration and her tool
#: CALLS keep 600: what she wrote is in the transcript when it was sent, and a
#: call's arguments are hers and short.
_BLOCK_CHARS = 600
_CARRIED_CHARS = 4000

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
ClipKind = Literal["reasoning", "tool_call", "tool_result", "thinking"]
CLIP_KINDS: tuple[str, ...] = get_args(ClipKind)

#: The metric issue #189 step 1 asks for: one clip, one increment, labelled by
#: `ClipKind`. Declared in `nora.obs.metric_specs` (append-only names); this is
#: the name the two modules must agree on.
_CLIPS_METRIC = "nora_debrief_clips_total"

#: The mark a clipped block carries, and the legend that explains it — issue
#: #189 step 2. `_TRANSCRIPT_LEGEND` below is the precedent this follows:
#: **telling the model what it is READING beat telling it what it can do**,
#: measured on the Relevance Gate (#78's closing comment). A block cut at
#: its bound and handed to a model with no mark reads as a complete account
#: that simply doesn't support the claim; the reader cannot
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
    # WITH the date (design/standing-compact, review 2026-09-24). A watermark
    # batch can span days, and the objective asks the fold to write dates
    # instead of «tomorrow»: a row stamped only «22:12» leaves a member's
    # «tomorrow» from three days back unconvertible, which is the Relevance
    # Gate's defect — a judgement about WHEN over an input with no date.
    return datetime.fromtimestamp(ts_ms / 1000, tz=tz).strftime("%Y-%m-%d %H:%M")


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
    """The turn's messages for the `<transcript>` block, so the delivery mark
    can never ship apart from the sentence explaining it (the block's own
    `fields=`).

    **There is no legend block.** It was a SIBLING of the transcript whose whole
    body was prose describing the transcript's fields (everything between the
    transcript's tags is a message somebody sent, so a sentence of ours cannot
    go in there). Its content is the type's own `fields=` now (#314): the thing
    that renders `delivery` is the thing that says what `delivery` means.
    """
    by_ts = first_by_ts(rows)

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


def _standing_prior(prior: Standing | None, *, now_ms: int) -> ContextBlock:
    """Her previous position, or the sentence saying there is none.

    This is HER fold from a previous turn, and the type says so — «yours to
    continue, not a record of what anybody said». The absent-position sentence
    is this module's own words about that absence, and since 2026-09-24 it is
    TRUE whenever it is rendered: nothing withholds an existing prior any more
    (design/standing-compact/10 §1).

    **`written` is how long before this fold the prior was written**
    (design/standing-compact/10 §2). The fold is told to re-judge every
    inherited clause, and a clause saying «tomorrow at 10» cannot be re-judged
    by a reader who does not know which day it was written on: a production
    position carried «today (6 September)» for 17 days, re-affirmed on every
    fold. The Relevance Gate's defect, one tier over — a judgement about WHEN
    over an input with no time. Same `age` vocabulary `<standing>` already
    uses on the tag turn.
    """
    if prior is None or not prior.body.strip():
        return _stamp(ContextBlock(
            name="standing_prior",
            body="(no position yet — this is the first fold for this conversation)",
        ))
    return _stamp(ContextBlock(
        name="standing_prior",
        attrs=(("written", age(
            prior.updated_at, datetime.fromtimestamp(now_ms / 1000, tz=UTC),
        )),),
        body=escape(prior.body.strip()),
    ))


def _standing_working_set_prior(
    working_set: tuple[WorkingSetItem, ...], *, now_ms: int,
) -> ContextBlock:
    """Her current working set, exactly as `_standing_prior` shows the
    paragraph — ALWAYS rendered, even empty, `_standing_prior`'s own reason:
    an absent block and a block the fold failed to read look identical from
    inside the prompt (§4.2), and the field's ordinary state is empty on
    most turns, which must not be indistinguishable from "the read failed".

    **Unfiltered by TTL, deliberately — unlike its `<standing_working_set>`
    sibling.** This is the fold's OWN re-judgment input, not a downstream
    reader's: the render function that builds `<standing_working_set>` for
    the tag/participation prompt filters on `now - found_at` before it
    builds the block (design §2), but the fold is where an item is meant to
    age OUT by the model's own judgement — "re-judge it the same way [as the
    paragraph]" — the same reason `_standing_prior` shows the WHOLE paragraph
    rather than pre-editing it for staleness.
    """
    if not working_set:
        return _stamp(ContextBlock(
            name="standing_working_set_prior",
            body="(no working set yet)",
        ))
    now = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    body = render_elements(
        working_set, tag="item",
        attrs=lambda item, _i: (
            ("source", item.source),
            ("found", age(item.found_at, now)),
            ("bears_on", item.bears_on),
        ),
        body=lambda item: item.what,
    )
    return _stamp(ContextBlock(name="standing_working_set_prior", body=body))


def _clip(
    text: str, *, kind: ClipKind, metrics: Metrics, limit: int = _BLOCK_CHARS,
) -> str:
    """Cut at `limit`, COUNTED and MARKED — issue #189 steps 1 and 2.

    The increment fires only on an actual cut, never on every call: a series
    that moved on every block, clipped or not, would say nothing about how
    often the ceiling is hit, which is the number step 3 needs. The mark is
    appended to the TEXT itself, ahead of the element render's escape, so it
    travels with the block through whichever prefix wraps it
    (`(called … with …)`, `(tool result: …)`) and reaches the model as part of what
    it is reading, not as a side channel it has no reason to trust more than
    the content.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    metrics.inc(_CLIPS_METRIC, block=kind)
    return f"{text[:limit]} …[{_CLIP_MARK}]"


def shown_step_tool_names(messages: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Every tool name this turn's `<steps>` block actually shows —
    `nora.engagement.stages.debrief`'s check that a working-set item's
    `source` references a step the fold was shown
    (design/standing-compact/30-working-set.md §3), rather than a paraphrase
    of the room's own question.

    The SAME `toolUse` blocks `_steps`/`_block` render as `(called NAME
    with …)`, walked independently here rather than threading a second
    return value through `_steps`'s string-building: a name set is a
    different shape from a rendered line, and computing it from the same raw
    `messages` the render already reads is what keeps the two from being able
    to disagree about what was actually called.
    """
    names: set[str] = set()
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, Sequence) or isinstance(content, str | bytes):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            use = block.get("toolUse")
            if not isinstance(use, Mapping):
                continue
            name = use.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name)
    return frozenset(names)


def _steps(messages: Sequence[Mapping[str, Any]], *, metrics: Metrics) -> str:
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
    rows: list[tuple[str, str, str | None]] = []
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
            rendered, kind = _block(block, metrics=metrics)
            if rendered:
                rows.append((role, rendered, kind))
    # ONE STEP, ONE ELEMENT — and this block is not all hers. A `toolResult`
    # from `load_transcript` or `who_said` is a member's own body and their
    # self-set profile name, so the flat `role: rendered` joined with newlines
    # let a quoted transcript line forge
    # `assistant: (called send_reply with …)` and `user: (tool result: …)` —
    # byte-identical to real steps, in the block the fold reads to decide what
    # happened, which then WRITES Standing. R-13c inside the one place the
    # conversion had framed as her own record.
    #
    # `kind` is set on a step of PRIVATE REASONING and on nothing else, and a
    # `None` drops the attribute (`render_elements`). So a turn whose model
    # produced no reasoning renders no `kind` anywhere and the block says
    # nothing about thinking; a model that does produce it is shown it, with
    # no change here or in the objective (operator 2026-09-24: «make it depend
    # on whether there is reasoning»).
    return render_elements(
        rows, tag="step",
        attrs=lambda r, _i: (("role", r[0]), ("kind", r[2])),
        body=lambda r: r[1],
    )


#: A URL, for `_clean_urls` below. Bounded on whitespace and the quoting a
#: JSON string or an HTML attribute puts around one, so it stops at the
#: closing quote rather than swallowing whatever JSON comes after it. The
#: backslash is excluded too: a re-serialised JSON string escapes an internal
#: `"` as `\"`, so the SAME url nested inside a `<untrusted source="...">`
#: attribute is followed by `\"` rather than a bare `"` — without excluding
#: the backslash, that copy's match ends one character later than the
#: top-level `"url"` field's, and the two copies compare unequal, so nothing
#: gets de-duplicated at all.
_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")


def _compact_json_or_text(text: str) -> str:
    """One `content[]` entry's `text`, as compactly as it can be shown.

    Issue #445's study: production tool results (`web_search`, `place_info`)
    return a raw JSON body as this `text`, and rendering the LIST it sits in
    with `str()` stacked Python's own repr-escaping on top of the JSON's —
    a nested HTML attribute like `source="URL"` reached the fold as
    `source=\\\\"URL\\\\"` inside a Python-list repr, spending the 4000-character
    clip on brackets and doubled backslashes before a single restaurant name.
    Text that parses as JSON is loaded and re-serialised compactly
    (`json.dumps(..., separators=(",", ":"))`), which keeps the one escaping
    layer JSON itself needs and removes that doubling. Text that does not
    parse — a plain status line, an `ambiguous` message — is used exactly as
    it was returned; there is nothing to compact.
    """
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def _clean_urls(text: str) -> str:
    """Percent-decode every URL in `text`, then drop a URL once it has already
    appeared once.

    Issue #445's study: the same page URL reaches the fold twice per search
    result — once as the result's own `url` field, once more inside the
    fetched page's `<untrusted source="...">` wrapper — and each is
    percent-encoded when the path holds non-ASCII (a Chinese restaurant
    listing's URL slug), which roughly triples its length. Neither copy is
    something the fold needs to carry into a position (§ "What does NOT
    belong" already bars a URL from the position itself); decoding shrinks
    each one and de-duplication removes the second copy outright, so more of
    the clip's fixed budget is left for content — a restaurant's name, not
    the address it was found at.
    """
    text = unquote(text)
    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        if url in seen:
            return "(same URL as above)"
        seen.add(url)
        return url

    return _URL_RE.sub(_replace, text)


def _tool_result_text(content: Any) -> str:
    """A tool result's `content`, as the fold actually needs it.

    Strands' own shape is a list, `[{"text": "<json or plain text>"}, ...]`;
    `str()` on that list is the shape issue #445's study found reaching the
    fold. Each `text` entry is compacted (`_compact_json_or_text`), the
    entries are joined, and the whole thing is URL-cleaned (`_clean_urls`). A
    `content` that is not this list shape — a plain string, which some tools
    return directly and which every fixture predating this shape still
    exercises — renders exactly as it did before, `str(content)`, URL-cleaned
    the same way.
    """
    if isinstance(content, Sequence) and not isinstance(content, str | bytes):
        parts = [
            _compact_json_or_text(item["text"])
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
            else str(item)
            for item in content
        ]
        text = "\n".join(parts)
    else:
        text = str(content)
    return _clean_urls(text)


def _block(
    block: Mapping[str, Any], *, metrics: Metrics,
) -> tuple[str, str | None]:
    """One content block as `(rendered, kind)`; `kind` is `"thinking"` for a
    step of private reasoning and `None` for everything else.

    RAW text: `render_elements` escapes every step body, so escaping here too
    rendered `<` as `&amp;lt;` — harmless to the boundary, but 4000 characters
    of web content reached the fold garbled and a URL it carried into the
    position came out mangled (review, 2026-09-24)."""
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return _clip(text, kind="reasoning", metrics=metrics), None
    reasoning = block.get("reasoningContent")
    if isinstance(reasoning, Mapping):
        inner = reasoning.get("reasoningText")
        raw = inner.get("text") if isinstance(inner, Mapping) else None
        if isinstance(raw, str) and raw.strip():
            return _clip(
                raw, kind="thinking", metrics=metrics, limit=_CARRIED_CHARS,
            ), "thinking"
    use = block.get("toolUse")
    if isinstance(use, Mapping):
        name = str(use.get("name") or "?")
        clipped = _clip(str(use.get("input")), kind="tool_call", metrics=metrics)
        return f"(called {name} with {clipped})", None
    result = block.get("toolResult")
    if isinstance(result, Mapping):
        clipped = _clip(
            _tool_result_text(result.get("content")), kind="tool_result",
            metrics=metrics, limit=_CARRIED_CHARS,
        )
        return f"(tool result: {clipped})", None
    return "", None


def _fold_rows(
    trace: TagTrace, since_rows: Sequence[TranscriptRow],
) -> tuple[TranscriptRow, ...]:
    """The transcript the tag-path fold actually sees: the watermark batch
    (`since_rows`, read via `RecordStore.since_including_nora`) PLUS the
    turn's own rows (`trace.transcript`), deduped by id and ascending.

    The turn's own rows are always included, even when the batch already
    covers them (D8): a fold must never review a turn it cannot see.
    """
    by_id = {row.id: row for row in since_rows}
    by_id.update({row.id: row for row in trace.transcript})
    return tuple(sorted(by_id.values(), key=lambda r: r.id))


def render_debrief_turn(
    *, trace: TagTrace, display_name: str, metrics: Metrics,
    since_rows: Sequence[TranscriptRow] = (), tz: tzinfo | None = None,
) -> str:
    """The whole turn — outcome, transcript, steps — as one block.

    `since_rows` is the watermark batch: every row since the last fold's
    `last_folded_row_id`, read via `RecordStore.since_including_nora` and
    capped at `[standing] fold_batch` (design/standing-watermark/20). The
    transcript is that batch PLUS the turn's own rows (`trace.transcript`),
    deduped by id — see `_fold_rows` for why the turn's rows must always be
    present.

    The fold prepends `<standing_prior>` to it (`render_debrief_input`).

    `metrics` is required, not defaulted — issue #189: a render this module
    cannot count is exactly the defect the issue is about, one level up from
    the model it feeds.

    What the clip mark MEANS is the `<steps>` type's `clipped` annotation,
    so it is on the block that can carry the mark and is present whether or not
    this turn clipped anything — the model reads the same contract every turn
    rather than learning a mark whose explanation appears only sometimes.

    It was a sibling `<how_to_read_reasoning>` block until #314 (and the block
    was `<reasoning>` until 2026-09-24, a name asserting a reasoning the
    deployed model does not produce): a block whose
    entire content was prose about the block beside it, kept next to it by an
    `order` number, which existed only because a field's meaning had nowhere to
    live. Travelling WITH the block is what a sibling could never do.
    """
    return _turn_block(
        trace=trace, display_name=display_name, metrics=metrics,
        since_rows=since_rows, tz=tz,
    ).render()


def _turn_block(
    *, trace: TagTrace, display_name: str, metrics: Metrics,
    since_rows: Sequence[TranscriptRow], tz: tzinfo | None,
) -> ContextBlock:
    """`render_debrief_turn`'s `<turn>`, as the block `render_debrief_input`
    assembles."""
    rows = _fold_rows(trace, since_rows)
    gloss = _OUTCOME_GLOSS.get(trace.outcome, trace.outcome)
    at = datetime.fromtimestamp(trace.created_at / 1000, tz=tz).strftime(
        "%Y-%m-%d %H:%M %Z"
    ).strip()
    inner = assemble([
        _stamp(ContextBlock(name="what_happened", body=escape(gloss))),
        _stamp(ContextBlock(
            name="transcript",
            body=_transcript(rows, display_name=display_name, tz=tz),
        )),
        _stamp(ContextBlock(
            name="steps",
            # MARKED, because a tool result is the room's words. `load_transcript`
            # and `who_said` hand back member-authored bodies and self-set
            # profile names, so this block carries what people typed even though
            # the steps around it are hers. `tests/test_prefill.py`'s own
            # classification names "a tool result" in the marked class; this
            # block was the exception to a rule the tree states in writing.
            body=_steps(trace.messages, metrics=metrics),
        )),
    ], sep="\n")
    return _stamp(ContextBlock(
        name="turn", attrs=(("outcome", trace.outcome), ("at", at)),
        # The ENVELOPE is this process's own record of a turn. The
        # member-authored text inside it is `<transcript>`, which carries its
        # own marker.
        body=inner,
    ))


def render_debrief_input(
    *,
    trace: TagTrace,
    prior: Standing | None,
    display_name: str,
    metrics: Metrics,
    since_rows: Sequence[TranscriptRow] = (),
    tz: tzinfo | None = None,
    culture: CultureRevision | None = None,
) -> str:
    """The whole of what the STANDING call sees — the prior plus the turn.

    `culture` (DEC-207) is the room's own account, rendered FIRST when the room
    has one: a position is where she had got to in this room, and the account
    is what the room is, which is how «had got to» is meant to be read. Absent,
    the prompt is byte-identical to the one before the tier was widened.

    `<standing_prior>` is emitted **even when there is none**, carrying an
    explicit "no position yet" rather than being omitted. §4.2 fixes that: an
    absent block and a block the fold failed to read look identical from inside
    the prompt, and the one thing this tier cannot afford is a fold that writes
    a second position beside the first because it never saw the first.

    The prior's age is measured against the TURN's own time, the same instant
    `<turn at=…>` renders, so the two clocks in one prompt cannot disagree.

    `<standing_working_set_prior>` sits right after `<standing_prior>`,
    `_standing_working_set_prior`'s own reason and the same always-emitted
    contract (design/standing-compact/30-working-set.md §4): "here is my
    position, here is what I have not told you" — before the turn being
    folded, which is what both are being re-judged AGAINST.
    """
    prior_block = _standing_prior(prior, now_ms=trace.created_at)
    working_set_prior_block = _standing_working_set_prior(
        prior.working_set if prior is not None else (), now_ms=trace.created_at,
    )
    turn = _turn_block(
        trace=trace, display_name=display_name, metrics=metrics,
        since_rows=since_rows, tz=tz,
    )
    return assemble(
        [
            *_culture_blocks(culture, now_ms=trace.created_at),
            prior_block, working_set_prior_block, turn,
        ],
        sep=_SEP,
    )


#: The blank line the f-strings put between this input's top-level blocks.
_SEP = "\n\n"


def _culture_blocks(
    culture: CultureRevision | None, *, now_ms: int,
) -> list[ContextBlock]:
    """The room's `<culture>` to put on top, or nothing when the room has no
    account (`render_culture`'s «no block, not an empty one»). Its age is
    measured against the same instant the rest of the prompt uses."""
    block = render_culture(
        culture, now=datetime.fromtimestamp(now_ms / 1000, tz=UTC))
    return [] if block is None else [_stamp(block)]


# ---------------------------------------------------------------------------
# The text the fold SENDS around the rendered input
# ---------------------------------------------------------------------------
#
# Moved here from `nora.engagement.stages.debrief` (`_prompt`,
# `_consolidate_prompt`, `_working_set_rule`) by step 2b of
# `design/context-assembly/04-every-occasion.md`: the stage runs the call and
# this module is the one place that says what the model reads. Two kinds of
# text, kept apart on purpose.
#
# * CONTEXT is the rendered input above (`render_debrief_input`), built from
#   blocks, and it is the USER message.
# * AUTHORED TEXT is the objective the operator wrote and the length rules this
#   module words, and it is the SYSTEM message: `system_prompt` below. None of
#   it is a block, because none of it is context: it tells the model what to
#   do, and a block's job is to say what something IS.
#
# The fold in `stages.debrief` and its retry call these, so the two cannot word
# the length rules differently.


def _working_set_rule(*, working_set_max_items: int, working_set_max_chars: int) -> str:
    """The working set's own length rule — `working_set_max_chars`/`_max_items`'s
    sentence, stated in both `fold_prompt` and `consolidate_prompt` the same way
    `max_chars`' rule already is, so the system message is consistent across
    the fold and its retry (design/standing-compact/30-working-set.md §1)."""
    return (
        f"The working set can hold at most {working_set_max_items} items "
        f"and must fit inside {working_set_max_chars} characters combined, "
        f"rendered as JSON. Leave it empty, or omit the key, when there is "
        f"nothing to carry."
    )


def system_prompt(
    *, instructions: str, max_chars: int,
    working_set_max_items: int, working_set_max_chars: int,
) -> str:
    """The SYSTEM message of both the fold and its retry: the objective, then
    the position's length rule, then the working set's."""
    rule = _working_set_rule(
        working_set_max_items=working_set_max_items,
        working_set_max_chars=working_set_max_chars,
    )
    return (
        f"{instructions}\n\n"
        f"The position you write must be at most {max_chars} characters.\n\n"
        f"{rule}"
    )


def fold_prompt(
    *, instructions: str, rendered: str, max_chars: int,
    working_set_max_items: int, working_set_max_chars: int,
) -> tuple[str, str]:
    """`(system, user)` — the objective and its length rule in the SYSTEM
    role, the turn being folded in the USER role.

    They were one concatenated string. That is not merely untidy: the rendered
    half is a transcript of what people typed, so with both in one user
    message an instruction written by a member sits in the same role as the
    operator's, and nothing downstream can tell which is which.
    """
    return (
        system_prompt(
            instructions=instructions, max_chars=max_chars,
            working_set_max_items=working_set_max_items,
            working_set_max_chars=working_set_max_chars,
        ),
        rendered,
    )


def consolidate_prompt(
    *, instructions: str, rendered: str, max_chars: int, over: str,
    working_set_max_items: int, working_set_max_chars: int,
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
    #
    # `over` is the model's own previous output, and it is appended exactly as
    # it came back: neither escaped nor wrapped in a block. That is what the
    # hand-built text did, and wrapping it is a model-visible change that
    # belongs in a step that says so, not in the one that moved this.
    return (
        system_prompt(
            instructions=instructions, max_chars=max_chars,
            working_set_max_items=working_set_max_items,
            working_set_max_chars=working_set_max_chars,
        ),
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
