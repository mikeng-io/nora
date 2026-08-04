"""Trigger matching (spec B.2 / R-11) and per-conversation debounce (R-9).

Firing is about *tagging intent*, not string presence: a trigger quoted as code is
not a tag (Q2).
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable, Hashable

# Zero-width and bidi-control characters used to smuggle a trigger past a naive match.
_INVISIBLE = dict.fromkeys(
    [*range(0x200B, 0x2010), *range(0x202A, 0x202F), 0x2060, 0xFEFF], None
)

_FENCED = re.compile(r"```.*?```|```.*\Z", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_WORD = re.compile(r"[0-9A-Za-z_]")


def normalize(body: str) -> str:
    """NFKC, then drop zero-width and bidi-control characters (B.2 rule 1)."""
    return unicodedata.normalize("NFKC", body).translate(_INVISIBLE)


def strip_code_spans(body: str) -> str:
    """Remove fenced blocks then inline spans (B.2 rule 2). Unclosed fences eat the tail."""
    return _INLINE_CODE.sub(" ", _FENCED.sub(" ", body))


def is_tagged(body: str, trigger: str) -> bool:
    if not body or not trigger:
        return False
    text = strip_code_spans(normalize(body))
    pattern = re.compile(re.escape(normalize(trigger)), re.IGNORECASE)
    for match in pattern.finditer(text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if _WORD.match(before) or _WORD.match(after):
            continue                       # B.2 rule 3: word-character boundary required
        return True
    return False


class Debounce:
    """R-9: a second tag for the same conversation inside the window is dropped.

    The clock is injected so tests are deterministic and never sleep. A blocked tag
    does NOT reset the window — otherwise a burst would starve the conversation.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = float(min_interval_seconds)
        self._clock = clock
        self._last: dict[Hashable, float] = {}

    def allow(self, key: Hashable) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and (now - last) < self._interval:
            return False
        self._last[key] = now
        return True

    def sweep(self, older_than: float) -> int:
        """Drop entries last seen more than `older_than` seconds ago (by the
        injected clock) and return how many were dropped.

        `_last` is otherwise unbounded — every distinct (platform, conversation_id)
        key it ever sees stays forever, so it grows with platforms x conversations.
        This is the hook `app/runtime.py` calls on an interval to keep it bounded;
        `Debounce` itself never calls this.
        """
        now = self._clock()
        stale = [key for key, last in self._last.items() if now - last > older_than]
        for key in stale:
            del self._last[key]
        return len(stale)
