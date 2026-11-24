"""Legacy Profile account text and the read-only heads port."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from nora.domain.character_card import format_card

from ..domain.profile import ProfileLine, ProfileView

log = logging.getLogger(__name__)

__all__ = [
    "PROFILE_ACCOUNT_MAX_CHARS",
    "ProfileHeads",
    "account_text",
]

PROFILE_ACCOUNT_MAX_CHARS = 2_500

#: The states a line renders in (45 §5): `current`, and `disputed` marked as
#: such. `hidden` (the subject's own dispute, 35 §2) and `outranked` (lost on
#: precedence, 45 §3) never render, and no terminal state does.
_RENDERED_STATES = frozenset({"current", "disputed"})

class ProfileHeads(Protocol):
    """The legacy store's read-only account heads."""

    def account_heads(self, *, key: str) -> Awaitable[tuple[ProfileLine, ...]]: ...


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
    """An account's plain text and its newest rendered timestamp."""
    body, written = _body(view, name_of=name_of, now_ms=int(now.timestamp() * 1000))
    if not body:
        return None
    return _cut(body, PROFILE_ACCOUNT_MAX_CHARS), written
