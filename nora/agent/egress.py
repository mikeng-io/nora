"""Outbound content that must never leave, whatever the model wrote.

Two predicates, because there are two questions and they have different
answers:

* `scan_identifiers` — somebody's E.164 number, ACI/PNI, or a key-shaped
  string. Never safe to emit **or transmit**, whatever the destination: the
  group, a tool result the model then reasons over, or an argument to a third
  party (design/14 §2.2). This is the floor.
* `scan_reply` — the identifiers **plus** the name of the model she runs on.
  Only for text addressed to the GROUP.

They were one function until 2026-08-06, and the difference is not cosmetic.
`_MODELNAME` matches nine ordinary English words — `claude`, `gemini`, `haiku`,
`opus`, `sonnet` among them — and `Deliverer.deliver` fails closed on the
WHOLE reply. Applied to a reply that is correct: what she runs on is nobody's
business. Applied anywhere else it costs a whole message or a whole memory for
a word that was never a disclosure. Two live consequences, both measured:
`about` (then called `recall`) withheld every durable conclusion
mentioning an LLM from a group of
software engineers, and 「搵首 haiku」 as a tool argument would have made her
mute. A third name for one of the two jobs is how the wrong one gets called
later, so there are exactly two and no alias.

`LeakGuard` (`nora.agent.guards`) already blocks the canary and verbatim
doctrine spans — R-6, protecting Nora's *configuration*. This protects the
*people*, which is a different exposure and was previously unguarded: nothing
stopped a reply from carrying somebody's phone number or ACI.

That gap matters more here than in most systems, because Nora legitimately
holds the material. The transcript she reasons over contains real sender ids,
and `about` returns durable recall about a person. `constraints.md`
tells her not to relay any of it — but a doctrine instruction is a request to a
model, and this codebase has now been bitten four separate times by things
that were true in intent and false in fact. A prompt-injected or simply sloppy
turn should not be the only thing between a member's phone number and the
group.

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

# What she runs on is nobody's business — IN A REPLY. `constraints.md` already
# forbids disclosing it, but that is a request to a model — and unlike the
# credential, this one IS reachable: the gateway echoes the upstream name back
# in every response, so it exists inside the process. `config.toml` names the
# gateway ALIAS rather than the model for the same reason; this is the backstop
# for the paths that alias cannot cover.
#
# It is also, unavoidably, a list of ordinary words. `haiku` is a poem, `opus`
# is a work, `gemini` is a star sign, and an engineer naming the model their
# own app calls is disclosing nothing about Nora. That is why this pattern sits
# in `scan_reply` alone: the rule is «she does not SAY it», not «the string
# never exists».
_MODELNAME = re.compile(
    r"\b(?:deepseek|gpt|claude|gemini|gemma|llama|qwen|"
    r"mistral|mixtral|grok|sonnet|opus|haiku|chatgpt|openai|anthropic)\b",
    re.IGNORECASE,
)

# Identifiers first, so that when a reply carries both a number and the word
# `claude`, `nora_egress_blocks_total{kind}` names the exposure that would have
# mattered. Both are blocked either way; only the label differs.
_IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", _E164),
    ("uuid", _UUID),
    ("keylike", _KEYLIKE),
)

_REPLY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *_IDENTIFIER_PATTERNS,
    ("model", _MODELNAME),
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

    `scan_identifiers` plus the model name (R-6/R-18). Only for text addressed
    to the group — everywhere else, use `scan_identifiers`.
    """
    return _first_match(text, _REPLY_PATTERNS)


def _normalize(text: str) -> str:
    """Collapse whitespace so a reflowed paste still matches (leak detection only)."""
    return _WS.sub(" ", text).strip()


# Markdown emphasis/heading syntax, as it actually reaches a chat client that
# does not render it. Deliberately NARROW — see `strip_markdown`.
#
# The delimiter rule is about the ASTERISK RUN and the whitespace INSIDE it,
# never about word characters outside it. The first version guarded with
# `(?<!\w)`/`(?!\w)` and was broken for Cantonese — the language she actually
# writes in (review 2026-08-10). `\w` is Unicode-aware, so 投 and 資 ARE word
# characters; in `**投資**方面` the closing `**` is followed by 方, the guard
# fails on the real delimiter, and the engine backtracks onto the INNER
# asterisk of each pair — emitting `*投資*方面`. Half-stripped output is worse
# than none: the asterisks still reach the group AND they move onto the wrong
# spans (`重點係**價錢**同**位置**` became `重點係*價錢同位置*`). Chinese prose has
# no spaces, so bold-adjacent-to-CJK is the COMMON shape here, not an edge case.
#
#   (?<!\*)(\*{1,3})(?!\*)  a COMPLETE run of 1-3 asterisks — this is what stops
#                           the engine matching half of a `**` pair, and what
#                           leaves `****` and `**/*.py` alone.
#   (?!\s)  …  (?<!\s)      no whitespace just inside either delimiter, which is
#                           what keeps `3 * 4` and `a * b * c` intact.
#   [^\n]+?                 single line only. Emphasis never spans a blank line,
#                           and the old `re.DOTALL` let two unrelated stray
#                           asterisks in different paragraphs pair up and eat
#                           the text between them.
_MD_EMPHASIS = re.compile(r"(?<!\*)(\*{1,3})(?!\*)(?!\s)([^\n]+?)(?<!\s)\1(?!\*)")
# `(?=\S)` so a bare `###` is left as literal text instead of being emptied —
# the strip must never turn a reply that would have been SENT into an R-4
# empty_reply refusal.
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(?=\S)", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Remove Markdown emphasis and headings from a reply (DEC-82).

    **A structural fix for a doctrine rule the model ignores.** `voice.md`
    already says «No headers. No bold labels. It's a chat message.» — and on
    long answers the model emits `**投資** — …` and `**燒烤堂** 🔥 — …` anyway,
    which Signal renders as literal asterisks. Measured live 2026-08-10: two of
    the four long replies in one stress-test session leaked raw `**`. DEC-78
    already established the lesson this applies — a prose maxim does not move
    behaviour, the matched surface does — so the rule is enforced where the text
    LEAVES rather than asserted once more in the prompt.

    NARROW on purpose, because this edits her authored words:
    - `**x**` / `*x*` / `***x***` → `x`, and leading `#` headings → nothing.
    - A `*` with whitespace inside the delimiters is left alone, so «3 * 4» and
      a bare "*" survive untouched.
    - `(?<!\\w)`/`(?!\\w)` keep it off word-internal asterisks, so a filename or
      a glob in code-ish text is not rewritten.
    - Underscores are NOT touched: `_x_` is far more often part of a real token
      (`person_id`, `send_reply`) than emphasis, and this repo's own vocabulary
      is full of them. Bold is what leaked; bold is what this removes.

    Not a leak guard — nothing here is unsafe, it is just the wrong FORMAT for
    the destination — so it returns cleaned text rather than refusing, and it is
    applied to the reply only, never to a tool argument."""
    text = _MD_HEADING.sub("", text)
    # Longest delimiters first: one pass over `***x***` with the `{1,3}` group
    # is greedy and already takes all three, but repeating until stable also
    # collapses `**a *b* c**` (nested emphasis), which one pass leaves half-done.
    for _ in range(3):
        cleaned = _MD_EMPHASIS.sub(r"\2", text)
        if cleaned == text:
            break
        text = cleaned
    return text


class LeakGuard:
    """R-6: no reply carries the canary or a verbatim >=min_span doctrine span.

    Doctrine sections only — runtime_context holds config values (display name,
    trigger) that are legitimately quotable. Somebody ELSE's identifiers are a
    different exposure, guarded separately by `nora.agent.egress` (R-18).
    """

    def __init__(self, doctrine: Doctrine, canary: str, min_span: int = 40) -> None:
        self._canary = canary
        self._min_span = min_span
        # The haystack speaks the SAME dialect as the candidate (fail-closed
        # audit 2026-08-10). DEC-82 strips Markdown from replies, but the
        # doctrine files ARE Markdown — voice.md alone carries 10 `**bold**`
        # spans and every file has `#` headings — so comparing a stripped reply
        # against a raw haystack silently un-blocked verbatim recitations whose
        # windows straddled a removed marker. Measured: 405 bold-caused and 831
        # heading-caused misses at a 45-char window. Both sides are normalised
        # the same way here; `Deliverer` additionally checks the RAW reply, so a
        # span is caught whichever dialect it was written in.
        self._haystack = _normalize(strip_markdown(doctrine.doctrine_text))
        self._raw_haystack = _normalize(doctrine.doctrine_text)

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
            if span in self._haystack or span in self._raw_haystack:
                log.error("doctrine_leak_blocked: verbatim span %r", span[:60])
                return "doctrine_span"
        return None
