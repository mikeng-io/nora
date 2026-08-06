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
`recall` withheld every durable conclusion mentioning an LLM from a group of
software engineers, and 「搵首 haiku」 as a tool argument would have made her
mute. A third name for one of the two jobs is how the wrong one gets called
later, so there are exactly two and no alias.

`LeakGuard` (`nora.agent.guards`) already blocks the canary and verbatim
doctrine spans — R-6, protecting Nora's *configuration*. This protects the
*people*, which is a different exposure and was previously unguarded: nothing
stopped a reply from carrying somebody's phone number or ACI.

That gap matters more here than in most systems, because Nora legitimately
holds the material. The transcript she reasons over contains real sender ids,
and `read_peer_card` returns durable recall about a person. `constraints.md`
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


class LeakGuard:
    """R-6: no reply carries the canary or a verbatim >=min_span doctrine span.

    Doctrine sections only — runtime_context holds config values (display name,
    trigger) that are legitimately quotable. Somebody ELSE's identifiers are a
    different exposure, guarded separately by `nora.agent.egress` (R-18).
    """

    def __init__(self, doctrine: Doctrine, canary: str, min_span: int = 40) -> None:
        self._canary = canary
        self._min_span = min_span
        self._haystack = _normalize(doctrine.doctrine_text)

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
                log.error("doctrine_leak_blocked: verbatim span %r", span[:60])
                return "doctrine_span"
        return None
