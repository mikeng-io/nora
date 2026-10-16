"""Outbound content that must never leave, whatever the model wrote.

Two predicates, because there are two questions and they have different
answers:

* `scan_identifiers` — somebody's E.164 number, ACI/PNI, or a key-shaped
  string. Never safe to emit **or transmit**, whatever the destination: the
  group, a tool result the model then reasons over, or an argument to a third
  party. This is the floor.
* `scan_reply` — the identifiers plus a coordinate pair, applied to text
  addressed to the GROUP. There is no model-name veto: `x_read` (DEC-151)
  legitimately relays third-party content that names models, and refusing the
  whole reply for a quoted model name would cost real answers.

`LeakGuard` (`nora.agent.guards`) blocks the canary and verbatim doctrine
spans — R-6, protecting Nora's *configuration*. This protects the *people*,
which is a different exposure: nothing else stops a reply from carrying
somebody's phone number or ACI.

That matters because Nora legitimately holds the material. The transcript she
reasons over contains real sender ids, and `about` returns durable recall about
a person. `constraints.md` tells her not to relay any of it — but a doctrine
instruction is a request to a model, not a guarantee. A prompt-injected or
simply sloppy turn should not be the only thing between a member's phone
number and the group.

**Blocking costs one silent reply. Leaking is not recoverable** — the message
is on every member's device before anyone can react. So this fails closed, and
the refusal is counted rather than swallowed.

Deliberately NOT matched here:

* Free-standing digit runs. Prices, dates, counts and version numbers are
  ordinary chat content, and blocking them would make her mute at random.
* The LLM credential. It is never in the model's context — it authenticates
  the gateway call and appears in no prompt — so a pattern for it would only
  ever fire on a false positive.
"""

from __future__ import annotations

import logging
import re

from ..doctrine.loader import Doctrine
from .markdown import strip_markdown
from .punctuation import undash

# E.164 as Signal actually presents it: a leading +, country code, 7-14 more
# digits. Requires the +, so "call me at 12345678" stays sendable while the
# canonical form a sender id or a contact record carries does not.
_WS = re.compile(r"\s+")

log = logging.getLogger("nora.guards")

_E164 = re.compile(r"\+\d{8,15}\b")

# An ACI/PNI. A bare UUID is never something the group needs said out loud —
# it is an identifier only Nora ever sees, which is exactly why it leaking
# would be hers to have leaked.
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# Conventional secret prefixes. Cheap to check and unambiguous when they hit.
_KEYLIKE = re.compile(r"\b(?:sk|pk|api|ghp|gho|xox[abps])[-_][A-Za-z0-9_-]{16,}\b")

# Identifiers first, so that when a reply carries both a number and the word
# `claude`, `nora_egress_blocks_total{kind}` names the exposure that would have
# mattered. Both are blocked either way; only the label differs.
_IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", _E164),
    ("uuid", _UUID),
    ("keylike", _KEYLIKE),
)

# DEC-156: where_been/where_is results CARRY lat/lng, so a sloppy or injected
# turn can echo a coordinate into a reply. The pair pattern is the same shape
# `looks_like_coordinate` (nora/agent/security.py) uses for tool ARGUMENTS; it
# is REDEFINED here rather than imported because security.py already imports
# from this module (a second import direction would be circular). Range is NOT
# checked: an out-of-range pair is still a leaked coordinate, and blocking it
# too is the safe direction.
_COORD_PAIR = re.compile(r"-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+")

# The reply scan is the identifier scan PLUS the coordinate pair: identifiers
# are the floor at every exit, and coordinates are a reply-only exposure
# because DEC-156 puts lat/lng into the model's context.
_REPLY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *_IDENTIFIER_PATTERNS,
    ("coordinate", _COORD_PAIR),
)


def _first_match(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    if not text:
        return None
    for kind, pattern in patterns:
        if pattern.search(text):
            return kind
    return None


def scan_identifiers(text: str) -> str | None:
    """Return the kind of identifier found, or None if the text carries none.

    The predicate for anything that is not a reply: a tool result the model
    will reason over, or an argument leaving for a third party. Somebody's
    number or ACI is unsafe at every one of those exits.
    """
    return _first_match(text, _IDENTIFIER_PATTERNS)


def scan_reply(text: str) -> str | None:
    """Return the kind of disclosure found, or None if the text is safe to SEND.

    The identifier scan plus the coordinate pair, scoped to text addressed to
    the group (R-6/R-18). There is no model-name veto. Everywhere else, use
    `scan_identifiers`.
    """
    return _first_match(text, _REPLY_PATTERNS)


def _normalize(text: str) -> str:
    """Collapse whitespace so a reflowed paste still matches (leak detection only)."""
    return _WS.sub(" ", text).strip()


#: The transcript element's opening tag, `<m n="38" ...>` (`nora.domain.blocks`,
#: `render_row_window` / `render_messages`). A member's body is XML-escaped when
#: rendered, so a literal `<m n=` in her draft can only have come from the
#: PROMPT, never from anything a member wrote. It is the deterministic tell of
#: prose that is her deliberation quoting a transcript row (recovered by the
#: DEC-93 delivery of a turn that ended without `send_reply`); deliberation
#: without one is not caught here.
_TRANSCRIPT_TAG = re.compile(r"<m\s+n\s*=")


class LeakGuard:
    """R-6: no reply carries the canary, a verbatim >=min_span doctrine span,
    or the prompt's own transcript markup (`<m n=...>`).

    Doctrine sections only — runtime_context holds config values (display name,
    trigger) that are legitimately quotable. Somebody ELSE's identifiers are a
    different exposure, guarded separately by `nora.agent.egress` (R-18).
    """

    def __init__(self, doctrine: Doctrine, canary: str, min_span: int = 40) -> None:
        self._canary = canary
        self._min_span = min_span
        # The haystack speaks the SAME dialect as the candidate. DEC-82 removes
        # Markdown SYNTAX from replies (emphasis becomes a style range, the
        # CHARACTERS go), and the doctrine files ARE Markdown (bold spans,
        # `#` headings), so a converted reply compared against a raw haystack
        # would miss verbatim recitations whose windows straddle a removed
        # marker. `strip_markdown` is the TEXT half of the very function that
        # produces the reply's form (`nora.agent.markdown`), so the two sides
        # cannot drift on what a delimiter is. `Deliverer` additionally checks
        # the RAW reply, so a span is caught whichever dialect it was written in.
        # The SAME punctuation too: the model reads the doctrine with its em
        # dashes turned into commas (`nora.agent.punctuation`), so a verbatim
        # recital carries the comma and a window across one would miss a
        # haystack that kept the dash.
        self._haystack = _normalize(undash(strip_markdown(doctrine.doctrine_text)))
        self._raw_haystack = _normalize(undash(doctrine.doctrine_text))

    def check(self, text: str) -> str | None:
        if not text:
            return None
        if self._canary and self._canary in text:
            return "canary"
        if _TRANSCRIPT_TAG.search(text):
            return "prompt_markup"
        candidate = _normalize(text)
        if len(candidate) < self._min_span:
            return None
        for start in range(0, len(candidate) - self._min_span + 1):
            span = candidate[start:start + self._min_span]
            if span in self._haystack or span in self._raw_haystack:
                log.error("doctrine_leak_blocked: verbatim span %r", span[:60])
                return "doctrine_span"
        return None
