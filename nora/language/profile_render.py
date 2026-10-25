"""Read Profile heads and wrap the shared Character card in prompt blocks.

The existing render switch and read timeout still apply. Assembly filters
location through the room flag and DisclosureGuard before this renderer;
the shared formatter renders profile facts, dated dimensions and open
guesses. Authored identity remains the leading account text. Account and
turn budgets omit whole lines so modes, exceptions and predictions survive.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Protocol

from nora.domain.character_card import format_card

from ..domain.blocks import ContextBlock
from ..domain.profile import ProfileLine, ProfileView, identity_text
from ..domain.prompt import age as _age
from ..domain.prompt import escape
from ..obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "PROFILE_ACCOUNT_MAX_CHARS",
    "PROFILE_PEOPLE_MAX_CHARS",
    "PROFILE_READ_TIMEOUT_SECONDS",
    "PROFILE_SELF_MAX_CHARS",
    "ProfileHeads",
    "account_text",
    "authored_identity",
    "read_profile",
    "render_profile",
    "render_profile_people",
]

#: How long any occasion waits for a Profile read before giving the block up
#: — `DEFAULT_CULTURE_READ_TIMEOUT_SECONDS`'s value and argument.
PROFILE_READ_TIMEOUT_SECONDS = 5.0

#: The most characters ONE account's body may carry, and the most Nora's own
#: may — `PERSON_ACCOUNT_MAX_CHARS` and `SELF_MAX_CHARS`'s values, for their
#: argument: the block rides every model call of a turn, so the render is
#: capped whatever the store holds. Identity storage is unbounded; Character
#: and guess caps remain separate, and a cap is not a reservation.
#: ponytail: constants, not config; a knob only if a real account is cut.
PROFILE_ACCOUNT_MAX_CHARS = 2_500
PROFILE_SELF_MAX_CHARS = 4_000

#: The most the turn's PERSON blocks may be together (wrappers, ages, escaped
#: bodies and markers counted) — `CHARACTER_BLOCK_MAX_CHARS`'s value and
#: argument. Nora's, the room's and the unit's blocks do not spend it: they
#: are one each, per-account capped, and sit where the blocks they replace sat.
PROFILE_PEOPLE_MAX_CHARS = 12_000

#: Room kept for the closing `[N more characters not shown]` line, as
#: `culture_render._CLOSING_MARKER_RESERVE`.
_CLOSING_MARKER_RESERVE = len("\n[00000000 more characters not shown]")

#: The states a line renders in (45 §5): `current`, and `disputed` marked as
#: such. `hidden` (the subject's own dispute, 35 §2) and `outranked` (lost on
#: precedence, 45 §3) never render, and no terminal state does.
_RENDERED_STATES = frozenset({"current", "disputed"})

_TERMINAL_PUNCT = ("?", "？", "!", "！", "。", ".")  # noqa: RUF001 — the full-width ones are sentence enders too


class ProfileHeads(Protocol):
    """The one Profile read any prompt needs — `SqlProfileStore.account_heads`
    — narrowed to a Protocol for `culture_render._HeadReader`'s reason: a
    reader wired this way can never write a line."""

    def account_heads(self, *, key: str) -> Awaitable[tuple[ProfileLine, ...]]: ...


async def read_profile(
    heads: Awaitable[tuple[ProfileLine, ...]] | None,
    *,
    stage: str,
    platform: str,
    metrics: Metrics | None,
    timeout_seconds: float = PROFILE_READ_TIMEOUT_SECONDS,
) -> tuple[ProfileLine, ...] | None:
    """One account's chain heads for a prompt, or `None` — every occasion's
    ONE Profile read, `read_culture`'s shape (DEC-207): bounded, counted,
    fail-soft, each occasion naming its `stage`.

    `heads` is the caller's own `reader.account_heads(key=…)` call, or `None`
    when there is no reader or no key (`[profile] render` off, a room outside
    the policy). The CALLER makes the call rather than handing the reader
    over, for `read_culture`'s reason: a store call stays on
    `self.<collaborator>` where
    `tests/architecture/test_dream_store_calls_are_declared.py` can count it.

    `None` collapses three states the prompt cannot tell apart: no reader, no
    key, and a failed read. Only the failed read is counted, under
    `nora_errors_total{stage}` (R-27). An account that does not exist yet is
    `()`, not an error: nothing was written in shadow for this key, which is
    the ordinary state before the backfill and says so by rendering no block.
    """
    if heads is None:
        return None
    try:
        return await asyncio.wait_for(heads, timeout=timeout_seconds)
    except Exception:
        log.exception("%s: profile read failed; continuing without it", stage)
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


def _identity_text(line: ProfileLine, name_of: Callable[[str], str]) -> str:
    text = identity_text(line, name_of=name_of)
    if line.mode == "told" and line.told_by is not None:
        text += f" (told by {name_of(line.told_by)})"
    return text


def _body(
    view: ProfileView,
    *,
    name_of: Callable[[str], str],
    now_ms: int,
) -> tuple[str, int | None]:
    """The block body and the newest rendered line's `written_at` (None when
    only authored lines render: an authored line carries no time, and a
    fabricated one would read as freshness the account does not have)."""
    sections = []
    remaining = 1200  # The shared card's existing total text budget.
    for part, label in (("facts", "facts"), ("traits", "traits"),
                        ("guesses", "guesses (unconfirmed)")):
        text = format_card(tuple(x for x in view.lines if x.part == part),
                           now_ms=now_ms, name_of=name_of, location=True,
                           max_chars=remaining)
        remaining -= len(text)
        rows = ([*view.authored] if part == "facts" else []) + text.splitlines()
        sections.append(label + ":\n" + "\n".join("- " + row for row in rows))
    if not view.authored and not any("\n- " in section for section in sections):
        return "", None
    body = "\n".join(section.rstrip() for section in sections)
    written = max((x.written_at for x in view.lines
                   if x.state in _RENDERED_STATES and x.text), default=None)
    return body, written


def _cut(body: str, room: int) -> str:
    """`body` cut to `room` characters, saying how many it dropped."""
    if len(body) <= room:
        return body
    kept: list[str] = []
    for line in body.splitlines():
        candidate = "\n".join([*kept, line])
        dropped = len(body) - len(candidate)
        if len(candidate) + len(f"\n[{dropped} more characters not shown]") > room:
            break
        kept.append(line)
    text = "\n".join(kept)
    marker = f"[{len(body) - len(text)} more characters not shown]"
    return "\n".join([*kept, marker]) if len(marker) <= room else ""


def account_text(
    view: ProfileView, *, name_of: Callable[[str], str], now: datetime,
) -> tuple[str, int | None] | None:
    """One account's sections as plain TEXT for a tool result
    (`character_read` under the switch): `render_profile`'s body without the
    block wrapper — a tool result is not a prompt block — capped at
    `PROFILE_ACCOUNT_MAX_CHARS` with the cut marker, with the newest rendered
    line's `written_at` so the result can carry its own `as_of` (an account
    carrying no time would read as fresher than it is). `None` when the
    account would say nothing, `render_profile`'s rule."""
    body, written = _body(view, name_of=name_of, now_ms=int(now.timestamp() * 1000))
    if not body:
        return None
    return _cut(body, PROFILE_ACCOUNT_MAX_CHARS), written


def render_profile(
    view: ProfileView, *, name_of: Callable[[str], str], now: datetime,
    max_chars: int | None = None,
) -> ContextBlock | None:
    """One entity's `<profile>` block, or `None` when it would say nothing.

    `None` for an account with no renderable lines and no authored lines, for
    `render_culture`'s reason: an entity with no account yet is not an entity
    she looked at and found featureless, and an empty block would say she
    had. Capped at `max_chars` (`PROFILE_SELF_MAX_CHARS` for Nora's own
    account, `PROFILE_ACCOUNT_MAX_CHARS` otherwise); a cut body says how much
    it dropped. The block's `order`/`key` are the prompt's own and are
    stamped by the caller (the replaced block's row), never here.
    """
    cap = max_chars if max_chars is not None else (
        PROFILE_SELF_MAX_CHARS if view.entity == "nora" else PROFILE_ACCOUNT_MAX_CHARS
    )
    body, written = _body(view, name_of=name_of, now_ms=int(now.timestamp() * 1000))
    if not body:
        return None
    attrs: tuple[tuple[str, str], ...] = (
        ("of", "nora" if view.entity == "nora" else view.of),
        ("written_by", "nora"), ("source", "what people said in chat"),
    )
    attrs += (("written", _age(written, now) if written is not None else "unknown"),)
    shown = _cut(body, cap)
    if not shown:
        return None
    return ContextBlock(name="profile", attrs=attrs, body=escape(shown))


def render_profile_people(
    views: Sequence[ProfileView], *, name_of: Callable[[str], str], now: datetime,
) -> tuple[ContextBlock, ...]:
    """The turn's PERSON blocks under their one shared budget
    (`PROFILE_PEOPLE_MAX_CHARS`), `render_character`'s algorithm one block up:
    accounts in the order given, the first the remaining budget cannot hold
    whole is CUT to what is left, every later one is left out whole and
    counted in one closing `[N more characters not shown]` block, whose room
    is reserved, so the group never exceeds the cap."""
    budget = PROFILE_PEOPLE_MAX_CHARS - _CLOSING_MARKER_RESERVE

    def rendered(view: ProfileView, room: int) -> ContextBlock | None:
        return render_profile(view, name_of=name_of, now=now, max_chars=room)

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
        room = min(len(body), PROFILE_ACCOUNT_MAX_CHARS)
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
            name="profile", body=f"[{omitted} more characters not shown]",
        ))
    return tuple(blocks)
