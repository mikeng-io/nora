"""One determination — a turn that could have spoken and decided either way —
as a value, and the two renderings that read it.

**Why this is a value and not two format calls.** The shadow mirror (design/
dreamer-agent-loop/35 §10.6) already rendered a determination, and rendered it
well: the source room, the verdict, the text she sent or the draft she declined
to send, and the window that closed. But it rendered it *into a send* — through
`deliver.build`, the `Deliverer`'s guards and an audit row — so the whole record
existed only where an operator had wired a mirror channel, cost a real send per
determination, and never appeared in the log. A determination that is only
readable as a chat message is not readable by anything that is not a person
already in that channel.

Splitting the value from its rendering gives the same record two sinks: the
mirror it already had, and the process log, which needs no channel and no send.
The mirror's output is unchanged — `render_mirror` is the old `_mirror_text`
verbatim, and the fire path's shadow tests read it.

**Why this is not a metric** (AGENTS.md: «a log line is not a metric»). The
counters already exist and already answer how many, how often, with what
outcome: `nora_participation_spoke_total`, `nora_participation_silence_total`,
and the proactive pair. What no counter can carry is WHAT the declined floor was
made of — the draft that doubles as her reasoning, and the window she read it
against. That content is the whole reason to look, and it is irreducible to a
label.

**Why the content is DEBUG and the shape is INFO.** `FirePath._turn` already
made this call once, deliberately: the length of unrouted prose goes to INFO
«because it separates them without putting group speech in the log; the prose
itself is DEBUG, for a deliberate diagnosis». A determination carries both her
draft and a tail of the room's transcript, so it splits the same way — `log_determination`
logs the shape at INFO and the speech at DEBUG, as two events with fixed
shapes rather than one event whose fields come and go with the log level.

**No `nora.*` imports, on purpose.** This module reads dataclasses it defines
and the standard library, nothing else. A determination is not a Nora idea —
it is what any occasion that may decline the floor produces — and keeping the
imports empty is what lets it be lifted out whole.
`test_the_module_imports_nothing_from_nora` is the check, and it is why the
ONE-ROW-ONE-LINE collapse every other renderer calls is not called here: see
`render_mirror` and `window_from` below for where it happens instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

#: How many transcript rows the window tail carries, and how much of each.
#: Both are the shadow mirror's own numbers, kept because the two sinks render
#: ONE value: a log that truncated differently would be a second answer to
#: «what did she read» and the two would drift.
WINDOW_ROWS = 5
WINDOW_BODY_CHARS = 120

Verdict = Literal["spoke", "silent"]

#: The ledger's key column: the four outcomes the mirror's prose already
#: distinguishes, as one word each. Derived from the value in `disposition`
#: rather than passed in, so a caller cannot label a determination something
#: its own fields contradict.
Disposition = Literal["spoke", "held_shadow", "held_cooldown", "declined"]


@dataclass(frozen=True, slots=True)
class WindowRow:
    """One row of the window that closed, flattened.

    Deliberately not `TranscriptRow`: this module carries the two fields the
    rendering reads and nothing else, which is what keeps it free of the
    domain package. `sender_name` is `str | None` because the record store's
    row is — an unmapped sender has no name, and rendering it as `None` is
    what the mirror has always done.
    """

    sender_name: str | None
    #: ONE LINE, ALREADY. `render_mirror` puts this on a line of its own beside
    #: a name, so a body holding a line break forges a window row under a name
    #: that never said it. The collapse is `nora.domain.prompt.one_line` and it
    #: belongs to whoever builds this — this module may not import it (see the
    #: module docstring) — so `window_from`, the only constructor, does it
    #: rather than asking its caller to remember. An obligation stated in a
    #: comment is the kind that gets forgotten; that is this whole change's
    #: story.
    body: str | None


@dataclass(frozen=True, slots=True)
class Determination:
    """One occasion that could have spoken, and what it decided.

    `texts` is the sent messages for a `spoke` verdict; for a `silent` one it
    is the held text (what she WOULD have said, kept by the shadow gate or the
    send cooldown) or the decline draft — the prose she wrote instead of
    calling `send_reply`, which doubles as her reasoning for declining.

    `held` and `shadow_hold` are separate because the operator's question is
    different in each case: `held` says she concluded speak, `shadow_hold` says
    the room heard nothing because shadow mode is on rather than because the
    cooldown happened to hold it (Miles decision 2026-09-05).
    """

    occasion: str
    platform: str
    conversation_id: str
    verdict: Verdict
    texts: tuple[str, ...]
    held: bool
    shadow_hold: bool
    stop_reason: str | None
    window: tuple[WindowRow, ...]

    @property
    def disposition(self) -> Disposition:
        """The four-way outcome, derived — never stored."""
        if self.verdict == "spoke":
            return "spoke"
        if self.held:
            return "held_shadow" if self.shadow_hold else "held_cooldown"
        return "declined"


def render_mirror(d: Determination) -> str:
    """The shadow mirror post (§10.6): the source room, the verdict, the text
    and the window that closed.

    Byte-identical to the `_mirror_text` this replaced. `tests/unit/
    test_fire_path.py`'s shadow suite reads these strings.
    """
    lines = [f"[shadow] {d.platform}/{d.conversation_id}"]
    if d.verdict == "spoke":
        lines.append("verdict: SPOKE — the room heard:")
    elif d.held:
        if d.shadow_hold:
            lines.append(
                "verdict: SILENT — she concluded speak; SHADOW MODE held it "
                "(the room hears nothing until the flip). She would have said:"
            )
        else:
            lines.append(
                "verdict: SILENT — she concluded speak; the send cooldown held "
                "it. She would have said:"
            )
    else:
        lines.append("verdict: SILENT — she declined the floor.")
        if d.texts:
            lines.append("her draft (never sent; doubles as her reasoning):")
    lines.extend(d.texts)
    if d.stop_reason:
        lines.append(f"(stop: {d.stop_reason})")
    if d.window:
        lines.append("window that closed:")
        # ONE ROW, ONE LINE — and the collapse is the CALLER's, which is the
        # only shape available to a module that may not import `nora.*`. These
        # are one line per window row in a post an operator reads the verdicts
        # off, so a newline in a member's message writes an extra
        # `- somebody: …` line under a name that never said it. `window_from`
        # is the only way a window is ever built and it collapses every body
        # there, so a row reaching this line has already been through it.
        lines.extend(
            f"- {row.sender_name}: {(row.body or '')[:WINDOW_BODY_CHARS]}"
            for row in d.window
        )
    return "\n".join(lines)


def as_shape(d: Determination) -> dict[str, Any]:
    """The determination without any speech in it — the INFO record.

    Everything here is either a label or a count. It answers «which occasion,
    in which room, decided what, and how much did it hold back» without
    putting a word the group said, or a word she wrote, into the log.
    """
    return {
        "occasion": d.occasion,
        "platform": d.platform,
        "conversation_id": d.conversation_id,
        "verdict": d.verdict,
        "disposition": d.disposition,
        "text_count": len(d.texts),
        "text_chars": sum(len(t) for t in d.texts),
        "stop_reason": d.stop_reason,
        "window_rows": len(d.window),
    }


def as_content(d: Determination) -> dict[str, Any]:
    """The speech the shape left out — the DEBUG record.

    Carries the same `occasion`/`conversation_id` pair as `as_shape` so the
    two events join, and then the two things only a determination has: what
    she would have said, and the tail of what she read before deciding not to.
    """
    return {
        "occasion": d.occasion,
        "conversation_id": d.conversation_id,
        "disposition": d.disposition,
        "texts": list(d.texts),
        "window": [
            {
                "sender_name": row.sender_name,
                "body": (row.body or "")[:WINDOW_BODY_CHARS],
            }
            for row in d.window
        ],
    }


def log_determination(log: logging.Logger, d: Determination) -> None:
    """Log one determination: the shape at INFO, the speech at DEBUG.

    Two events with fixed shapes rather than one event whose fields appear and
    disappear with the log level — a record that changes shape cannot be read
    by anything that did not also read the level it was written at.

    `tag_id` is not a field here: `nora.obs.logging` binds it as a contextvar
    for the whole turn, so every line already carries it. Repeating it would be
    a second copy that a refactor can make disagree with the first.
    """
    log.info("determination", extra=as_shape(d))
    if log.isEnabledFor(logging.DEBUG):
        log.debug("determination.content", extra=as_content(d))


def window_from(rows: Any) -> tuple[WindowRow, ...]:
    """The last `WINDOW_ROWS` of a transcript, flattened to this module's shape.

    Takes anything with `sender_name` and `body` — `TranscriptRow` at runtime —
    so the domain type stays on the caller's side of the import.

    **Bodies are collapsed to one line here**, which is what makes
    `WindowRow.body`'s promise true. `str.splitlines` is the stdlib answer to
    «what ends a line», so rejoining its output with a space needs no regex, no
    table of separators and no `nora.*` import — see `_one_line` below.

    Doing it here rather than asking the caller to is the same judgement
    `MediaJoin` is one class for: one renderer remembering and the next
    forgetting is what this change exists to stop, and this is the only way a
    `Determination`'s window is ever built.
    """
    return tuple(
        WindowRow(
            sender_name=row.sender_name,
            body=None if row.body is None else _one_line(row.body),
        )
        for row in list(rows)[-WINDOW_ROWS:]
    )


def _one_line(body: str) -> str:
    """`body` with every line break replaced by the space a reader would see.

    **Not a second copy of a policy** — that is the distinction that makes this
    acceptable where a duplicated separator table would not be. The policy is
    «what ends a line», and `str.splitlines` IS that answer, in the standard
    library, for both this function and `nora.domain.prompt.one_line`. There is
    no list here that can drift out of step with one over there, because
    neither of them holds a list.

    `tests/unit/test_determination.py` pins the agreement directly against
    `one_line` over a shared corpus, so a divergence reds rather than waits to
    be noticed.
    """
    return " ".join(part.strip() for part in body.splitlines() if part.strip())
