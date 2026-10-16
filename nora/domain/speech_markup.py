"""MiniMax speech markup: what a spoken reply may carry beyond her words, and
the check that it carries nothing else.

Her reply is written chat. Before it reaches the TTS voice, the speech
formatter (`nora.agent.speech_format`) may ADD three kinds of thing MiniMax's
`t2a_v2` reads as performance rather than text, and may not touch a word:

- a pause, `<#x#>`, x in seconds;
- an interjection tag from the closed list `speech-2.8-*` supports;
- nothing else. (Pronunciation entries ride the request's
  `pronunciation_dict`, configured statically, not in the text.)

Everything here is deterministic and framework-free, so it is the half of the
formatter a test can pin exactly. The model's output is accepted only when
`check` finds no reason to refuse it; a refusal speaks `normalise(text)`, which
is her words with only the unreadable removed. The vendor's rules are quoted
from platform.minimax.cn/docs/api-reference/speech-t2a-http:
a pause is 0.01 to 99.99 seconds with at most two decimals, must sit between
pronounceable text, and may not be used consecutively.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, get_args

#: The interjection tags `speech-2.8-hd` / `speech-2.8-turbo` perform, as the
#: vendor lists them. Closed: a tag not in this set is read ALOUD as a word
#: («giggles»), so the check treats it as an added word, which it is.
INTERJECTIONS: frozenset[str] = frozenset({
    "laughs", "chuckle", "coughs", "clear-throat", "groans", "breath", "pant",
    "inhale", "exhale", "gasps", "sniffs", "sighs", "snorts", "burps",
    "lip-smacking", "humming", "hissing", "emm", "sneezes",
})

#: Our bound on one pause, stricter than the vendor's 99.99: a reply that goes
#: quiet for longer than this in a voice channel reads as a dropped line.
MAX_PAUSE_SECONDS = 2.0
#: At most this many interjections in one reply. One laugh is a laugh; five is
#: a model performing.
MAX_INTERJECTIONS = 2
#: At most one pause per this many spoken characters (letters and digits).
CHARS_PER_PAUSE = 3

#: Every outcome of one formatting attempt, the label vocabulary of
#: `nora_speech_format_total`. DERIVED by `get_args`, so the help text and the
#: increments cannot disagree.
#:
#: - `formatted`: the model added markup and the check passed.
#: - `unchanged`: the check passed and the model added nothing.
#: - `short` / `long`: under `min_chars` or over `max_chars`; no model call,
#:   the normalised text is spoken.
#: - `words_changed`, `bad_markup`, `empty`, `truncated`, `timeout`, `error`,
#:   `no_instruction`: the normalised text is spoken. Never a dropped reply.
FormatOutcome = Literal[
    "formatted", "unchanged", "short", "long", "words_changed", "bad_markup", "empty",
    "truncated", "timeout", "error", "no_instruction",
]
FORMAT_OUTCOMES: tuple[str, ...] = get_args(FormatOutcome)

#: Any `<#…#>` at all, valid or not, so a malformed one is SEEN rather than
#: left in the text to be read aloud as punctuation.
_ANY_PAUSE = re.compile(r"<#([^#<>]*)#>")
_PAUSE_VALUE = re.compile(r"[0-9]{1,2}(?:\.[0-9]{1,2})?")
_TAG = re.compile(
    r"\((" + "|".join(sorted(map(re.escape, INTERJECTIONS))) + r")\)"
)

# Markdown that is markup in chat and noise in speech. Removed as MARKERS
# only: a line's number («1. ») is a word and stays, and so does a code
# fence's language word (backticks go with `_MD_EMPHASIS`, never the text
# after them).
_MD_LINE_PREFIX = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|[-*+•][ \t]+|>[ \t]?)")
_MD_EMPHASIS = re.compile(r"\*\*|__|~~|`")
#: Symbols nobody says. `#` goes with them, which also disarms any `<#…#>` her
#: own text happened to contain: the only pauses spoken are ones `check` saw.
_STRAY = re.compile("[*_#~\uff5e|^`]")
#: Emoji glue: zero-width joiner, variation selectors, keycap.
_EMOJI_GLUE = re.compile("[‍︎️⃣]")
#: Symbols a voice SAYS, kept although they are `So`: «32℃» is not «32».
_SAID_SYMBOLS = frozenset("°℃℉")
#: A number as written, sign and separators included. Its run of digits is in
#: the skeleton already; what this adds is the punctuation INSIDE it, which
#: changes how it is read («3.14» vs «314», «-5» vs «5», «12345» vs «12 345»).
_NUMBER = re.compile(r"[-+\u2212]?\d+(?:[.,:/]\d+)*")
_SPACES = re.compile(r"[ \t　]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def _is_spoken(ch: str) -> bool:
    """A letter, a digit, or a combining mark on one: what a voice actually
    pronounces. Marks count because in Thai, Devanagari or decomposed kana a
    dropped mark is a changed word; the emoji glue marks (variation selector,
    keycap) are not speech and do not."""
    if _EMOJI_GLUE.match(ch):
        return False
    return unicodedata.category(ch)[0] in "LNM"


def spoken_skeleton(text: str) -> str:
    """The letters and digits of `text`, in order, nothing else. Two texts
    with the same skeleton say the same words in the same order; punctuation,
    spacing, emoji and markup do not reach it."""
    return "".join(ch for ch in text if _is_spoken(ch))


def normalise(text: str) -> str:
    """Her reply with only what must not be read removed: emoji, markdown
    markers, stray symbols. It never removes a letter or a digit
    (`spoken_skeleton(normalise(t)) == spoken_skeleton(t)` for every `t`,
    pinned by test), so it can be spoken with no model's say-so."""
    out = _MD_LINE_PREFIX.sub("", text)
    out = _MD_EMPHASIS.sub("", out)
    out = _STRAY.sub("", out)
    out = _EMOJI_GLUE.sub("", out)
    # Pictographs and the skin-tone modifiers are symbols (`So`, `Sk`). Some
    # are letters' neighbours in no language she writes, so removing the
    # whole category is safe; `_is_spoken` never matches them.
    out = "".join(ch for ch in out if ch in _SAID_SYMBOLS or not _unsaid_symbol(ch))
    out = _SPACES.sub(" ", out)
    out = _BLANK_LINES.sub("\n", out)
    return "\n".join(line.strip() for line in out.split("\n")).strip()


def _unsaid_symbol(ch: str) -> bool:
    return ch not in _SAID_SYMBOLS and unicodedata.category(ch) in ("So", "Sk", "Cs", "Co")


def strip_markup(text: str) -> str:
    """`text` with every well-formed pause and every allowed interjection
    removed. A malformed pause or an unknown tag is left in place, so it
    shows up as a difference instead of vanishing."""
    out = _ANY_PAUSE.sub(
        lambda m: "" if _PAUSE_VALUE.fullmatch(m.group(1)) else m.group(0), text,
    )
    return _TAG.sub("", out)


def _pause_ok(raw: str) -> bool:
    if not _PAUSE_VALUE.fullmatch(raw):
        return False
    seconds = float(raw)
    return 0.01 <= seconds <= MAX_PAUSE_SECONDS


RejectReason = Literal["empty", "bad_markup", "words_changed"]


def check(base: str, candidate: str) -> RejectReason | None:
    """Why `candidate` may NOT be spoken in place of `base`, or None.

    `base` is `normalise(reply)`. The candidate passes only if removing its
    markup leaves exactly `base`'s letters and digits in the same order, and
    every piece of markup is one MiniMax performs and placed where it may sit.
    Whitespace and punctuation may differ (a pause can stand where a comma
    was); a word may not. Fail-closed: anything this cannot classify is a
    refusal, and a refusal speaks `base`.
    """
    if not candidate.strip():
        return "empty"
    if len(candidate) >= 10_000:
        return "bad_markup"
    pauses = _ANY_PAUSE.findall(candidate)
    if any(not _pause_ok(p) for p in pauses):
        return "bad_markup"
    stripped = strip_markup(candidate)
    if "<#" in stripped or "#>" in stripped:
        return "bad_markup"
    # Nothing `normalise` would have removed may come back: an emoji or a
    # stray symbol the model added is not markup MiniMax performs.
    if _STRAY.search(stripped) or any(_unsaid_symbol(ch) for ch in stripped):
        return "bad_markup"
    tags = _TAG.findall(candidate)
    # Tags her own text already carried are hers, not the formatter's.
    added_tags = len(tags) - len(_TAG.findall(base))
    if added_tags > MAX_INTERJECTIONS:
        return "bad_markup"
    if spoken_skeleton(stripped) != spoken_skeleton(strip_markup(base)):
        return "words_changed"
    # A number is a word whose punctuation is part of it.
    if _NUMBER.findall(stripped) != _NUMBER.findall(strip_markup(base)):
        return "words_changed"
    spoken = len(spoken_skeleton(stripped))
    if pauses and len(pauses) > max(1, spoken // CHARS_PER_PAUSE):
        return "bad_markup"
    # «Only between pronounceable text, never consecutively»: every stretch a
    # pause opens or closes must hold a spoken character. A pause first, last
    # or beside another pause leaves an empty stretch.
    if pauses:
        stretches = _ANY_PAUSE.split(_TAG.sub("", candidate))[::2]
        if any(not spoken_skeleton(s) for s in stretches):
            return "bad_markup"
    return None


def adds_markup(base: str, candidate: str) -> bool:
    """True when the candidate carries a pause or a tag `base` did not."""
    return bool(_ANY_PAUSE.search(candidate)) or (
        len(_TAG.findall(candidate)) > len(_TAG.findall(base))
    )


__all__ = [
    "CHARS_PER_PAUSE", "FORMAT_OUTCOMES", "INTERJECTIONS", "MAX_INTERJECTIONS",
    "MAX_PAUSE_SECONDS", "FormatOutcome", "RejectReason", "adds_markup",
    "check", "normalise", "spoken_skeleton", "strip_markup",
]
