"""What the debrief is actually SHOWN — the rendering contract from
`design/dreamer-agent-loop/26-standing.md` §4.2, in its own module because the
gap between "the type carries everything" and "something renders it" is how the
fact tier failed: identity present on the row, dropped at the prompt, noticed
months later (`21-substrate-contract.md` §7.1).

**Six mappings, each one a defect another tier has already shipped.** They are
tested individually rather than through the whole block, because a render that
gets five right and drops the sixth still looks correct:

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
from typing import Any

from nora.domain.models import TranscriptRow
from nora.domain.prompt import escape, escape_attr
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace

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
_OUTCOME_GLOSS: Mapping[str, str] = {
    "send_reply": "you replied, through the send_reply tool",
    "recovered": "you wrote a reply as prose without calling send_reply; "
                 "the pipeline delivered it for you",
    "silent": "you chose to say nothing — no delivery was attempted (R-5)",
    "vetoed": "you tried to reply and every attempt was refused by a guard",
    "timeout": "the model call did not finish in time; nothing was sent",
    "llm_error": "the model call failed; nothing was sent",
}

#: How much of one narration/tool-result block reaches the fold. The trace is
#: already capped on the way into retention (`cap_messages`), so this is the
#: SECOND bound and it exists for a different reason: retention's budget is
#: about what is worth keeping, this one is about what fits beside a transcript
#: and a prior position inside one bounded completion. Generous enough that a
#: tool result stays readable, tight enough that one runaway result cannot
#: crowd out the exchange it belongs to.
_BLOCK_CHARS = 600


def _clock(ts_ms: int, tz: tzinfo | None) -> str:
    """`ts` as a local wall-clock time — the first of the six mappings.

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
    return row.person_name or row.sender_name or "unknown"


def _transcript(
    rows: Sequence[TranscriptRow], *, display_name: str, tz: tzinfo | None
) -> str:
    by_ts = {row.ts: row for row in rows}
    lines = []
    for row in rows:
        who = escape(_speaker(row, display_name))
        # The reply relation, by the parent's own rendered CLOCK rather than by
        # its id — the fold has the clock on every line above, and has no id
        # for anything. An out-of-window or self-referential pointer renders
        # nothing at all rather than a "?" (`prefill._parent_of`'s rule: a
        # symbol that can mean "unknown" invites the model to emit one).
        parent = by_ts.get(row.quoted_ts) if row.quoted_ts is not None else None
        if parent is not None and parent.id != row.id:
            who += f" (replying to {_clock(parent.ts, tz)})"
        lines.append(f"[{_clock(row.ts, tz)}] {who}: {escape(row.body or '')}")
    return "\n".join(lines)


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _BLOCK_CHARS else text[:_BLOCK_CHARS] + " …"


def _reasoning(messages: Sequence[Mapping[str, Any]]) -> str:
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
    lines: list[str] = []
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
            rendered = _block(block)
            if rendered:
                lines.append(f"{role}: {rendered}")
    return "\n".join(lines)


def _block(block: Mapping[str, Any]) -> str:
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return escape(_clip(text))
    reasoning = block.get("reasoningContent")
    if isinstance(reasoning, Mapping):
        inner = reasoning.get("reasoningText")
        raw = inner.get("text") if isinstance(inner, Mapping) else None
        if isinstance(raw, str) and raw.strip():
            return f"(thinking) {escape(_clip(raw))}"
    use = block.get("toolUse")
    if isinstance(use, Mapping):
        name = escape(str(use.get("name") or "?"))
        return f"(called {name} with {escape(_clip(str(use.get('input'))))})"
    result = block.get("toolResult")
    if isinstance(result, Mapping):
        return f"(tool result: {escape(_clip(str(result.get('content'))))})"
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
    *, trace: TagTrace, display_name: str, since_rows: Sequence[TranscriptRow] = (),
    tz: tzinfo | None = None,
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
    """
    rows = _fold_rows(trace, since_rows)
    gloss = _OUTCOME_GLOSS.get(trace.outcome, trace.outcome)
    at = datetime.fromtimestamp(trace.created_at / 1000, tz=tz).strftime(
        "%Y-%m-%d %H:%M %Z"
    ).strip()
    return (
        f'<turn outcome="{escape_attr(trace.outcome)}" at="{escape_attr(at)}">\n'
        f"  <what_happened>{escape(gloss)}</what_happened>\n"
        f"  <transcript>\n{_transcript(rows, display_name=display_name, tz=tz)}\n"
        f"  </transcript>\n"
        f"  <reasoning>\n{_reasoning(trace.messages)}\n  </reasoning>\n"
        f"</turn>"
    )


def render_verdict_input(
    *, trace: TagTrace, display_name: str, since_rows: Sequence[TranscriptRow] = (),
    tz: tzinfo | None = None,
) -> str:
    """What the VERDICT call sees: the whole turn, WITHOUT the prior.

    Same transcript, same reasoning, same outcome labelling as the standing
    render — the judge sequences the SAME turn, so a different render here
    would let the two calls disagree about what even happened. Only the prior
    is missing, and that is the point (issue #78 Phase 2): the verdict must not
    read the position it is about to write. `since_rows` is the SAME watermark
    batch the standing call reads (D4: the two calls must not disagree about
    what happened).
    """
    return render_debrief_turn(
        trace=trace, display_name=display_name, since_rows=since_rows, tz=tz,
    )


def render_debrief_input(
    *,
    trace: TagTrace,
    prior: Standing | None,
    display_name: str,
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
    prior_block = (
        f"<standing_prior>\n{escape(body)}\n</standing_prior>"
        if body
        else "<standing_prior>\n(no position yet — this is the first fold for "
             "this conversation)\n</standing_prior>"
    )
    turn = render_debrief_turn(
        trace=trace, display_name=display_name, since_rows=since_rows, tz=tz,
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
        f"<standing_prior>\n{escape(body)}\n</standing_prior>"
        if body
        else "<standing_prior>\n(no position yet — this is the first fold for "
             "this conversation)\n</standing_prior>"
    )
    transcript = _transcript(rows, display_name=display_name, tz=tz)
    return (
        f"{prior_block}\n\n"
        f"<conversation>\n  <transcript>\n{transcript}\n  </transcript>\n"
        f"</conversation>"
    )


def render_rollup_verdict_input(
    *, rows: Sequence[TranscriptRow], display_name: str, tz: tzinfo | None = None,
) -> str:
    """What the event-path fold's VERDICT call sees: the SAME batch WITHOUT the
    prior (D4 — the verdict must not read the position it is about to write,
    and the two calls must not disagree about what happened).
    """
    transcript = _transcript(rows, display_name=display_name, tz=tz)
    return (
        f"<conversation>\n  <transcript>\n{transcript}\n  </transcript>\n"
        f"</conversation>"
    )
