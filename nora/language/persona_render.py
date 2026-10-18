"""Reads and renders Persona accounts into `<persona>` blocks
(design/unified-memory/45 §5).

One block per entity in the turn, replacing `<peer_card>`, `<peer_history>`
and `<person_account>` (and the collective `<culture>`, `<unit_culture>` and
`<self>` views) on the graph memory surface, when `[persona] render` is on.
Until that switch the persona tables are written in shadow and nothing here
runs (45 §6: shadow changes no prompt).

The read mirrors `read_culture` (DEC-207): one bounded, counted, fail-soft
read shared by every occasion, each occasion naming its `stage`. The render
mirrors `render_character`: every block carries a `written` age when it holds
written lines, a body cut says how many characters it dropped, and the
persons of a turn share one block budget.

What a block says (45 §5): `identity:` lines (authored first, with no mode,
then told lines with their teller named), `character:` traits, and `open
questions:` the hypotheses — a hypothesis is a question, never a fact (45
§3), so it renders only here, only while its prediction window is open, and
it is given a question mark when its text lacks one. A `disputed` line is
marked as such and keeps rendering (35 §2: awaiting weighing); a `hidden`
line never renders (the subject disputed it); an `outranked` line lost on
precedence and is never rendered as a current fact. No ids, and no dates
beyond what a line itself says.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol

from ..domain.blocks import ContextBlock
from ..domain.persona import PersonaLine, PersonaView
from ..domain.prompt import age as _age
from ..domain.prompt import escape
from ..obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "PERSONA_ACCOUNT_MAX_CHARS",
    "PERSONA_PEOPLE_MAX_CHARS",
    "PERSONA_READ_TIMEOUT_SECONDS",
    "PERSONA_SELF_MAX_CHARS",
    "PersonaHeads",
    "authored_identity",
    "read_persona",
    "render_persona",
    "render_persona_people",
]

#: How long any occasion waits for a Persona read before giving the block up
#: — `DEFAULT_CULTURE_READ_TIMEOUT_SECONDS`'s value and argument.
PERSONA_READ_TIMEOUT_SECONDS = 5.0

#: The most characters ONE account's body may carry, and the most Nora's own
#: may — `PERSON_ACCOUNT_MAX_CHARS` and `SELF_MAX_CHARS`'s values, for their
#: argument: the block rides every model call of a turn, so the render is
#: capped whatever the store holds (the line caps bound it only at 25·300 +
#: 5·300 + 3·300 characters, and a cap is not a reservation).
#: ponytail: constants, not config; a knob only if a real account is cut.
PERSONA_ACCOUNT_MAX_CHARS = 2_500
PERSONA_SELF_MAX_CHARS = 4_000

#: The most the turn's PERSON blocks may be together (wrappers, ages, escaped
#: bodies and markers counted) — `CHARACTER_BLOCK_MAX_CHARS`'s value and
#: argument. Nora's, the room's and the unit's blocks do not spend it: they
#: are one each, per-account capped, and sit where the blocks they replace sat.
PERSONA_PEOPLE_MAX_CHARS = 12_000

#: Room kept for the closing `[N more characters not shown]` line, as
#: `culture_render._CLOSING_MARKER_RESERVE`.
_CLOSING_MARKER_RESERVE = len("\n[00000000 more characters not shown]")

#: The states a line renders in (45 §5): `current`, and `disputed` marked as
#: such. `hidden` (the subject's own dispute, 35 §2) and `outranked` (lost on
#: precedence, 45 §3) never render, and no terminal state does.
_RENDERED_STATES = frozenset({"current", "disputed"})

_TERMINAL_PUNCT = ("?", "？", "!", "！", "。", ".")


class PersonaHeads(Protocol):
    """The one Persona read any prompt needs — `SqlPersonaStore.account_heads`
    — narrowed to a Protocol for `culture_render._HeadReader`'s reason: a
    reader wired this way can never write a line."""

    def account_heads(self, *, key: str) -> Awaitable[tuple[PersonaLine, ...]]: ...


async def read_persona(
    reader: PersonaHeads | None,
    *,
    key: str,
    stage: str,
    platform: str,
    metrics: Metrics | None,
    timeout_seconds: float = PERSONA_READ_TIMEOUT_SECONDS,
) -> tuple[PersonaLine, ...] | None:
    """One account's chain heads for a prompt, or `None` — every occasion's
    ONE Persona read, `read_culture`'s shape (DEC-207).

    `None` is only "no reader" (`[persona] render` off, the shadow default)
    or a FAILED read; the failed read is counted under
    `nora_errors_total{stage}`, because an account that exists and could not
    be fetched reported as «there is none» is `unavailable` substituted for
    `nothing_found` (R-27). An account that does not exist yet is `()`, not
    an error: nothing was written in shadow for this key, which is the
    ordinary state before the backfill and says so by rendering no block.
    """
    if reader is None:
        return None
    try:
        return await asyncio.wait_for(
            reader.account_heads(key=key), timeout=timeout_seconds,
        )
    except Exception:
        log.exception("%s: persona read failed; continuing without it", stage)
        if metrics is not None:
            metrics.inc("nora_errors_total", stage=stage, platform=platform)
        return None


def authored_identity(
    *, name: str, aliases: Sequence[str] = (), platforms: Sequence[str] = (),
) -> tuple[str, ...]:
    """The authored identity lines of one person (45 §3): their names and
    their accounts, rendered from `identity.toml`, never written as lines and
    carrying no mode. `platforms` is display names only ("Signal", "Discord")
    — never a handle, a number or a uuid (R-18)."""
    lines = []
    if aliases:
        lines.append(f"known as {name} (" + ", ".join(aliases) + ")")
    if platforms:
        lines.append("on " + ", ".join(platforms))
    return tuple(lines)


def _identity_text(line: PersonaLine, name_of: Callable[[str], str]) -> str:
    text = " ".join(line.text.split())
    if line.mode == "told" and line.told_by is not None:
        text += f" (told by {name_of(line.told_by)})"
    return text


def _body(
    view: PersonaView, *, name_of: Callable[[str], str], now_ms: int,
) -> tuple[str, int | None]:
    """The block body and the newest rendered line's `written_at` (None when
    only authored lines render: an authored line carries no time, and a
    fabricated one would read as freshness the account does not have)."""
    identity: list[str] = list(view.authored)
    character: list[str] = []
    questions: list[str] = []
    written: int | None = None
    for line in view.lines:
        if line.state not in _RENDERED_STATES:
            continue
        disputed = " (disputed)" if line.state == "disputed" else ""
        if line.part == "hypothesis":
            # A hypothesis renders only while its prediction window is open
            # (45 §2: it holds its slot until then), and as a QUESTION (45
            # §3), whatever its verdict: confirmed stays a hypothesis.
            if line.prediction_window_end is None or line.prediction_window_end <= now_ms:
                continue
            text = " ".join(line.text.split())
            if not text.endswith(_TERMINAL_PUNCT):
                text += "?"
            questions.append(text + disputed)
        elif line.part == "identity":
            identity.append(_identity_text(line, name_of) + disputed)
        else:
            character.append(" ".join(line.text.split()) + disputed)
        if written is None or line.written_at > written:
            written = line.written_at
    sections = []
    if identity:
        sections.append("identity: " + " · ".join(identity))
    if character:
        sections.append("character: " + "; ".join(character))
    if questions:
        sections.append("open questions: " + " · ".join(questions))
    return "\n".join(sections), written


def _cut(body: str, room: int) -> str:
    """`body` cut to `room` characters, saying how many it dropped."""
    dropped = len(body) - room
    return body if dropped <= 0 else f"{body[:room]}\n[{dropped} more characters not shown]"


def render_persona(
    view: PersonaView, *, name_of: Callable[[str], str], now: datetime,
    max_chars: int | None = None,
) -> ContextBlock | None:
    """One entity's `<persona>` block, or `None` when it would say nothing.

    `None` for an account with no renderable lines and no authored lines, for
    `render_culture`'s reason: an entity with no account yet is not an entity
    she looked at and found featureless, and an empty block would say she
    had. Capped at `max_chars` (`PERSONA_SELF_MAX_CHARS` for Nora's own
    account, `PERSONA_ACCOUNT_MAX_CHARS` otherwise); a cut body says how much
    it dropped. The block's `order`/`key` are the prompt's own and are
    stamped by the caller (the replaced block's row), never here.
    """
    cap = max_chars if max_chars is not None else (
        PERSONA_SELF_MAX_CHARS if view.entity == "self" else PERSONA_ACCOUNT_MAX_CHARS
    )
    body, written = _body(view, name_of=name_of, now_ms=int(now.timestamp() * 1000))
    if not body:
        return None
    attrs: tuple[tuple[str, str], ...] = (("of", view.of),)
    if written is not None:
        attrs += (("written", _age(written, now)),)
    return ContextBlock(name="persona", attrs=attrs, body=escape(_cut(body, cap)))


def render_persona_people(
    views: Sequence[PersonaView], *, name_of: Callable[[str], str], now: datetime,
) -> tuple[ContextBlock, ...]:
    """The turn's PERSON blocks under their one shared budget
    (`PERSONA_PEOPLE_MAX_CHARS`), `render_character`'s algorithm one block up:
    accounts in the order given, the first the remaining budget cannot hold
    whole is CUT to what is left, every later one is left out whole and
    counted in one closing `[N more characters not shown]` block, whose room
    is reserved, so the group never exceeds the cap."""
    budget = PERSONA_PEOPLE_MAX_CHARS - _CLOSING_MARKER_RESERVE

    def rendered(view: PersonaView, room: int) -> ContextBlock | None:
        return render_persona(view, name_of=name_of, now=now, max_chars=room)

    blocks: list[ContextBlock] = []
    omitted = 0
    closed = False  # once an account is cut or left out, every later one is left out whole
    for view in views:
        body, _ = _body(view, name_of=name_of, now_ms=int(now.timestamp() * 1000))
        if not body:
            continue
        if closed:
            omitted += len(body)
            continue
        room = min(len(body), PERSONA_ACCOUNT_MAX_CHARS)
        shown = rendered(view, room)
        if shown is None:
            continue
        while len(shown.render()) + 1 > budget and room > 0:
            room = min(room - 1, max(0, room - (len(shown.render()) + 1 - budget)))
            shown = rendered(view, room)
            if shown is None:
                break
        if shown is None or len(shown.render()) + 1 > budget:
            closed = True
            omitted += len(body)
            continue
        blocks.append(shown)
        budget -= len(shown.render()) + 1
        if room < len(body):
            closed = True
    if omitted:
        blocks.append(ContextBlock(
            name="persona", body=f"[{omitted} more characters not shown]",
        ))
    return tuple(blocks)
