"""Guardrail enforcement (spec B). ReplyGuard is constructed fresh per tag, so counters
are per-invocation by construction rather than by bookkeeping.
"""

from __future__ import annotations

import logging
import re

from ..persona.loader import Persona

log = logging.getLogger("nora.guards")

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse whitespace so a reflowed paste still matches (leak detection only)."""
    return _WS.sub(" ", text).strip()


class LeakGuard:
    """R-6: no reply may carry the canary or a verbatim >=min_span span of persona text.

    Scope is the persona sections only — runtime_context holds config values
    (display name, trigger) that are legitimately quotable.
    """

    def __init__(self, persona: Persona, canary: str, min_span: int = 40) -> None:
        self._canary = canary
        self._min_span = min_span
        self._haystack = _normalize(persona.persona_text)

    def check(self, text: str) -> str | None:
        if not text:
            return None
        if self._canary and self._canary in text:
            return "canary"
        candidate = _normalize(text)
        if len(candidate) < self._min_span:
            return None
        for start in range(0, len(candidate) - self._min_span + 1):
            span = candidate[start:start + self._min_span]
            if span in self._haystack:
                log.error("persona_leak_blocked: verbatim span %r", span[:60])
                return "persona_span"
        return None
