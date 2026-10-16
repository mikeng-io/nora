"""One determination — a turn that could have spoken and decided either way —
as a value, and the two renderings that read it.

The value feeds two sinks: the shadow mirror (`render_mirror`, posted to a
channel) and the process log (`log_determination`), which needs no channel and
no send. A determination carries the source room, the verdict, the text she
sent or the draft she declined to send, and the window that closed.

**Not a metric** (AGENTS.md: «a log line is not a metric»). Counters such as
`nora_participation_spoke_total` and `nora_participation_silence_total` answer
how many and with what outcome. What no counter can carry is WHAT the declined
floor was made of: the draft that doubles as her reasoning, and the window she
read it against.

**Content is DEBUG, shape is INFO.** A determination carries her draft and a
tail of the room's transcript, so `log_determination` logs the shape at INFO
and the speech at DEBUG, as two events with fixed shapes rather than one event
whose fields come and go with the log level. This keeps group speech out of the
default log.

**No `nora.*` imports.** This module reads dataclasses it defines and the
standard library, nothing else, so it can be lifted out whole;
`test_the_module_imports_nothing_from_nora` checks it. For that reason it
renders no window of its own: `render_mirror` takes the window already
rendered (`nora.app.fire._mirror_window` builds it through the shared
`render_rows`), so the line format is not duplicated here.

The line COLLAPSE stays here, in `window_from` and `_one_line`.
`WindowRow.body` promises one line to everything that reads it, and the log
sink (`as_content`) reads it without passing through `fire`, so the promise is
kept at the constructor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

#: How many transcript rows the window tail carries, and how much of each.
#: Both sinks render ONE value, so the log and the mirror truncate identically.
WINDOW_ROWS = 5
WINDOW_BODY_CHARS = 120

Verdict = Literal["spoke", "silent"]

#: The ledger's key column: the four outcomes the mirror's prose distinguishes,
#: as one word each. Derived from the value in `disposition` rather than passed
#: in, so a caller cannot label a determination something its own fields
#: contradict.
Disposition = Literal["spoke", "held_shadow", "held_cooldown", "declined"]


@dataclass(frozen=True, slots=True)
class WindowRow:
    """One row of the window that closed, flattened.

    Deliberately not `TranscriptRow`: this module carries the two fields the
    rendering reads and nothing else, which is what keeps it free of the
    domain package. `sender_name` is `str | None` because the record store's
    row is: an unmapped sender has no name and renders as `None`.
    """

    sender_name: str | None
    #: ONE LINE, ALREADY. `render_mirror` puts this on a line of its own beside
    #: a name, so a body holding a line break would forge a window row under a
    #: name that never said it. `window_from`, the only constructor, does the
    #: collapse (this module may not import `nora.domain.prompt.one_line`).
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
    cooldown happened to hold it.
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


def render_mirror(d: Determination, *, window: str) -> str:
    """The shadow mirror post: the source room, the verdict, the text and the
    window that closed.

    `tests/unit/test_fire_path.py`'s shadow suite reads these strings.

    `window` is REQUIRED, with no default. It carries the rows this module may
    not render itself, so a default of `""` would let a caller that forgot the
    argument post a mirror with the window silently missing, indistinguishable
    from a turn where the window was genuinely empty. An empty string still
    renders nothing; the caller has to say so.
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
    if window:
        lines.append("window that closed:")
        lines.append(window)
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
    disappear with the log level.

    `tag_id` is not a field here: `nora.obs.logging` binds it as a contextvar
    for the whole turn, so every line already carries it.
    """
    log.info("determination", extra=as_shape(d))
    if log.isEnabledFor(logging.DEBUG):
        log.debug("determination.content", extra=as_content(d))


def window_from(rows: Any) -> tuple[WindowRow, ...]:
    """The last `WINDOW_ROWS` of a transcript, flattened to this module's shape.

    Takes anything with `sender_name` and `body` — `TranscriptRow` at runtime —
    so the domain type stays on the caller's side of the import.

    Bodies are collapsed to one line here, which is what makes
    `WindowRow.body`'s promise true (see `_one_line`). This is the only way a
    `Determination`'s window is built.
    """
    return tuple(
        WindowRow(
            # The name is collapsed too, and the `person_name` projection is
            # preferred over the self-set platform one (DEC-80):
            # `render_mirror` puts this on a line of its own beside a body, so
            # a newline in a profile name would forge a window row.
            sender_name=_one_line(
                getattr(row, "person_name", None) or row.sender_name or ""
            ) or None,
            body=None if row.body is None else _one_line(row.body),
        )
        for row in list(rows)[-WINDOW_ROWS:]
    )


def _one_line(body: str) -> str:
    """`body` with every line break replaced by the space a reader would see.

    Not a second copy of a policy: «what ends a line» is `str.splitlines`, for
    both this function and `nora.domain.prompt.one_line`, so neither holds a
    separator list that can drift.

    `tests/unit/test_determination.py` pins the agreement against `one_line`
    over a shared corpus.
    """
    return " ".join(part.strip() for part in body.splitlines() if part.strip())
