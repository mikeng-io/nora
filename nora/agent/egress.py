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
import unicodedata

from ..doctrine.loader import Doctrine
from .markdown import strip_markdown
from .punctuation import undash

# E.164 as Signal actually presents it: a leading +, then 6 to 15 digits, which
# is the span `Persons` accepts as an E.164 id (`nora.persons._E164`), so every
# direct address the guard can be handed is detected. The digits may carry
# separators (`+852 9000 0101`, `+1 (415) 555-0123`). Requires the +, so "call
# me at 12345678" stays sendable. The neighbours are ASCII-only: a `\b` boundary
# would let a CJK or other Unicode letter directly after the number escape.
_WS = re.compile(r"\s+")

log = logging.getLogger("nora.guards")

# Separators between digits: space, dot, brackets, slash, comma, a minus sign and
# every dash from U+2010 to U+2015 (hyphen, non-breaking, figure, en, em, bar).
_SEP = r"[\s.()\-/,\u2010-\u2015\u2212]"
_E164 = re.compile(rf"\+[0-9](?:{_SEP}*[0-9]){{5,14}}(?![0-9])")

# An ACI/PNI. A bare UUID is never something the group needs said out loud —
# it is an identifier only Nora ever sees, which is exactly why it leaking
# would be hers to have leaked. ASCII neighbours, as above.
_UUID = re.compile(
    r"(?<![0-9A-Za-z_])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9A-Za-z_])"
)

# Conventional secret prefixes. Cheap to check and unambiguous when they hit.
_KEYLIKE = re.compile(r"(?<![0-9A-Za-z_])(?:sk|pk|api|ghp|gho|xox[abps])[-_][A-Za-z0-9_-]{16,}")

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
_DEC = r"-?[0-9]{1,3}\.[0-9]+"
_LABEL = r"(?:lat(?:itude)?|lng|lon(?:gitude)?)"
# A compass letter or a degree sign after a decimal: `22.41N`, `114.12°E`.
_SUFFIX = r"°?(?:(?-i:[NSEW])(?![A-Za-z]))?"
# One element of a pair. `_WIDE` is a longitude's or latitude's width: a three-digit
# integer part (longitude runs to 180) or a fraction of three or more digits. A pair
# needs one of them, so a score, a time or a version (`14.30 16.00`, `3.11, 3.12`)
# is not a coordinate, while `22.41, 114.12` and `1.111 2.222` are.
_WIDE = rf"-?(?:[0-9]{{1,3}}\.[0-9]{{3,}}|[0-9]{{3}}\.[0-9]+){_SUFFIX}"
_NARROW = rf"{_DEC}{_SUFFIX}"
# Between the two elements, at least one separator or label: a dot is not one, so
# `2026.10.22` (a date) is not a pair. The outer ends are not part of a longer
# number or a version (`1.2.3.4`), but a sentence's full stop is still allowed.
_JOIN = rf"(?:[\s,;:/|=()\[\]]|{_LABEL})+"
_COORD_PAIR = re.compile(
    rf"(?<![0-9])(?<![0-9]\.)(?:{_WIDE}{_JOIN}{_NARROW}|{_NARROW}{_JOIN}{_WIDE})(?![0-9])(?!\.[0-9])",
    re.IGNORECASE,
)
# A labelled decimal on its own: `lat: 22.410`, `LNG = 114.124`.
_COORD_LABELLED = re.compile(
    rf"(?<![0-9A-Za-z_]){_LABEL}[\s:=/|()\[\]]*{_DEC}", re.IGNORECASE,
)
# `long` is not in `_LABEL` (it would take `long: 5.5`, a length, as a coordinate).
# It is a label only with `:` or `=`, and only a value of three decimals is one.
_COORD_LONG = re.compile(r"(?<![0-9A-Za-z_])long\s*[:=]\s*-?[0-9]{1,3}\.[0-9]{3,}", re.IGNORECASE)
# The Cantonese form: 北緯 (north latitude), 南緯, 東經 (east longitude), 西經. The
# words name a coordinate, so a decimal after one is not ordinary chat.
_COORD_HEMISPHERE = re.compile(rf"(?:北緯|南緯|東經|西經)\s*{_DEC}")

# The reply scan is the identifier scan PLUS the coordinate forms: identifiers
# are the floor at every exit, and coordinates are a reply-only exposure
# because DEC-156 puts lat/lng into the model's context.
_REPLY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *_IDENTIFIER_PATTERNS,
    ("coordinate", _COORD_PAIR),
    ("coordinate", _COORD_LABELLED),
    ("coordinate", _COORD_LONG),
    ("coordinate", _COORD_HEMISPHERE),
)

#: Variation selectors (U+FE00..U+FE0F and U+E0100..U+E01EF): they change how a
#: glyph is drawn, never what it says.
_VARIATION_RANGES = (("\ufe00", "\ufe0f"), ("\U000e0100", "\U000e01ef"))
#: Categories a reader does not see: format characters (`Cf`) and the combining
#: marks (`Mn`, `Me`, `Mc`) that sit on a base letter.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Mn", "Me", "Mc"})


def _canonical(text: str, *, split: bool = False) -> str:
    """The form every disclosure check compares. Whatever a reader sees is
    unchanged, so a single character cannot change the verdict: NFKC (full-width
    letters and digits, compatibility forms), every decimal digit of any script
    as its ASCII value, and the invisible characters removed: format (`Cf`),
    variation selectors and combining marks (`Mn`, `Me`, `Mc`). ASCII text is
    already canonical.

    `split` decomposes first (NFKD), so a mark on a letter with a precomposed form
    (`A` plus an acute, which NFKC would compose into `Á`) is still a mark and is
    removed. The identifier patterns do not use it: their lookarounds are ASCII-only,
    and a decomposed `é` would read to them as an ASCII `e`. The LeakGuard
    comparisons have no lookarounds, so they can."""
    if text.isascii():
        return text
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD" if split else "NFKC", text):
        category = unicodedata.category(ch)
        if category in _INVISIBLE_CATEGORIES or any(lo <= ch <= hi for lo, hi in _VARIATION_RANGES):
            continue
        out.append(str(unicodedata.decimal(ch)) if category == "Nd" else ch)
    return "".join(out)


def _first_match(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    if not text:
        return None
    text = _canonical(text)
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
        self._canon_canary = _canonical(canary, split=True)
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
        # The same two haystacks in canonical form, for the canonical candidate
        # (`check`): a zero-width character inside a verbatim span is not a
        # different span.
        self._canon_haystack = _canonical(self._haystack, split=True)
        self._canon_raw_haystack = _canonical(self._raw_haystack, split=True)

    def check(self, text: str) -> str | None:
        if not text:
            return None
        # Both forms are checked, the text as given and its canonical form, so a
        # canary or a verbatim span with an invisible character or a full-width
        # digit inside it still matches, and the original-form check is kept.
        canon = _canonical(text, split=True)
        if (self._canary and self._canary in text) or (
            self._canon_canary and self._canon_canary in canon
        ):
            return "canary"
        if _TRANSCRIPT_TAG.search(text) or _TRANSCRIPT_TAG.search(canon):
            return "prompt_markup"
        for candidate, haystacks in (
            (_normalize(text), (self._haystack, self._raw_haystack)),
            (_normalize(canon), (self._canon_haystack, self._canon_raw_haystack)),
        ):
            span = _verbatim_span(candidate, haystacks, self._min_span)
            if span is not None:
                log.error("doctrine_leak_blocked: verbatim span %r", span[:60])
                return "doctrine_span"
        return None


def _verbatim_span(candidate: str, haystacks: tuple[str, str], min_span: int) -> str | None:
    """The first `min_span` window of `candidate` that a haystack holds verbatim."""
    if len(candidate) < min_span:
        return None
    for start in range(0, len(candidate) - min_span + 1):
        span = candidate[start:start + min_span]
        if any(span in haystack for haystack in haystacks):
            return span
    return None
