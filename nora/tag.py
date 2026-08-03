"""Trigger matching (spec B.2 / R-11) and per-conversation debounce (R-9).

Firing is about *tagging intent*, not string presence: a trigger quoted as code is
not a tag (Q2).
"""

from __future__ import annotations

import re
import time
import unicodedata

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
    """Ignore rapid re-tags in the same group. Rate limiting, not conversation state."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval = min_interval_seconds
        self._last: dict[str, float] = {}

    def allow(self, group_id: str) -> bool:
        now = time.monotonic()
        last = self._last.get(group_id, 0.0)
        if now - last < self.min_interval:
            return False
        self._last[group_id] = now
        return True
