"""What every `SearchProvider` adapter holds identically, so a backend swap is a
wiring change and not a behaviour change (DEC-157).

Two adapters live behind one port (`TavilySearchProvider`, `ExaSearchProvider`)
and a third would make three. What belongs here is only what MUST NOT differ
between them:

* **The `unavailable` copy.** These strings are model-facing instruction — they
  are what stops Nora answering as if she had looked something up (R-5/R-27).
  If they drifted per backend, «I couldn't check» would mean a different thing
  depending on which vendor the operator picked, which is exactly the property
  the port exists to deny.
* **The clamp.** A tool result is re-sent on every subsequent model call, so
  what accumulates must be bounded (the fetch-token lesson, 2026-08-08). The
  bound is configured on `[search]` itself rather than per backend for the same
  reason, and the function that applies it lives once.

Deliberately NOT here: the envelope parsing, the auth header, the request body.
Those are each vendor's own shape and pretending otherwise is how an adapter
grows a branch per provider inside a "shared" helper.
"""

from __future__ import annotations

from typing import Any

from ...domain.blocks import wrap_outside_text

__all__ = ["UNAVAILABLE_FETCH", "UNAVAILABLE_SEARCH", "clamp_chars", "wrap"]

# Re-exported so an adapter imports its wrapper from the search package. It
# used to reach into `nora.infra.transit.arrivals` for a PRIVATE helper across
# two infra packages; wrapping one piece of outside text as a block is
# `nora.domain.blocks`' subject and it lives there now. `name` is its first
# positional argument and has no default — see there for why (#314).
wrap = wrap_outside_text


def clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp `text` to `max_chars`, stating whether it was truncated."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


UNAVAILABLE_SEARCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't run that search just now. Say so — don't answer as if "
            "you'd looked it up.",
}

UNAVAILABLE_FETCH: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't open that link just now. Say so — don't answer as if "
            "you'd read it.",
}
