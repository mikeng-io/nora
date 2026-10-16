"""Cues: the deterministic features of a Record item that make it matter.

**Code, never a model.** A cue is computed once, at ingest, from the item's
own text, and stored on its stimulus. Nothing downstream re-reads the text to
decide whether it mattered; the wake evaluator and Cognition's agenda
compute on the cue names (comprehend once).

**A cue is a reason to look, not a conclusion.** «聽日» in a message does not
mean a plan exists; it means the item carries a date expression, which is why
it wakes the loop sooner (a strong cue). A false positive costs
one earlier session; a false negative costs nothing but lateness, because every
pending stimulus is still consumed by the next wake (settled, batch, max age).
So the vocabularies below lean towards recall on the phrases people in these
rooms actually type, in English and in Cantonese, and are deliberately NOT a
parser: they never extract WHICH date.

What is implemented, and what is not:

- a date or time expression: `datetime`, English and Cantonese/Chinese;
- an amount or a split: `amount`;
- a keep instruction («記低», "keep that", "write that down"): `keep`;
- an RSVP word: `rsvp`;
- a place name or a place link: `place_link` only, a maps or venue URL. A
  bare place NAME needs a gazetteer the Record does not hold;
- a surprise cue ("don't tell her", «唔好話俾佢知»): `surprise`;
- a reply to, edit of, reaction to or delete of a message a Register row
  cites: NOT computed. Nothing records which message a Register row cites, and
  faking it from «any reply» would make every reply strong.

`STRONG_CUES` are the ones the strong-cue trigger names: a date or time, a
place change (here: a place link), a keep instruction, an RSVP.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, get_args

#: Every cue a stimulus can carry, in the order they are stored. The label
#: vocabulary of `nora_stimulus_total{cue}` is this plus `none`, DERIVED here.
Cue = Literal["datetime", "amount", "keep", "rsvp", "place_link", "surprise"]
CUES: tuple[str, ...] = get_args(Cue)

#: The cues that trip the `strong cue` trigger. `amount` and `surprise`
#: matter to a session (a split to record; a surprise makes `exclude_persons`
#: mandatory on the write that cites it) but do not by themselves say a plan is
#: moving, so they wait for a settled room like any other stimulus.
STRONG_CUES: frozenset[str] = frozenset({"datetime", "keep", "rsvp", "place_link"})

#: After a wake whose reason was `strong_cue`, only these cues trip the trigger
#: again. A `datetime` mention in a conversation that just woke a session is
#: usually the same plan being talked on, and re-waking on each one slices one
#: conversation into many sessions. An RSVP (a
#: decision), a place link (a place) and a keep instruction («記低», DEC-306:
#: the tag turn tells the person it WILL be kept, and only a session keeps it)
#: are new information worth catching fast. The restriction lifts when a wake
#: of any other reason is the unit's newest.
REARMING_CUES: frozenset[str] = frozenset({"rsvp", "place_link", "keep"})

_WEEKDAYS_EN = (
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|mon|tue|tues|wed|thu|thur|thurs|fri"   # not `sat`/`sun`: ordinary words
)
_MONTHS_EN = (
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
    r"|january|february|march|april|june|july|august|september|october"
    r"|november|december"
)
_CN_NUM = r"[0-9一二三四五六七八九十兩两]"   # full-width digits: NFKC folds them

_DATETIME = re.compile(
    "|".join((
        # 7pm, 7 pm, 7:30pm, 19:30
        # Digit-led patterns use digit/letter lookarounds, not `\b`: a CJK
        # character is a word character, so «今晚7:30» has no `\b` before 7.
        r"(?<!\d)\d{1,2}(?::\d{2})?\s*(?:am|pm)(?![a-z])",
        r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])",
        # at 8, by 9, around 7
        r"\b(?:at|by|around|before|after|until|till)\s+\d{1,2}(?::\d{2})?\b",
        r"\b(?:noon|midnight|tonight|tonite|tomorrow|tmr|tmrw|today|yesterday)\b",
        r"\bthis\s+(?:morning|afternoon|evening|weekend|week)\b",
        r"\bnext\s+(?:week|month|weekend|year|" + _WEEKDAYS_EN + r")\b",
        r"\b(?:" + _WEEKDAYS_EN + r")\b",
        # 28/9, 28/9/2026, YYYY-MM-DD
        r"(?<![\d/])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\d/])",
        r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)",
        # Sep 28, 28 Sep, 28th of September
        r"\b(?:" + _MONTHS_EN + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?(?![\da-z])",
        r"(?<!\d)\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MONTHS_EN + r")\b",
        # in 20 mins, in 2 hours
        r"\bin\s+\d+\s*(?:min|mins|minutes|hr|hrs|hour|hours|day|days|week|weeks)\b",
        # Cantonese / Chinese relative days and parts of a day
        r"今日|今晚|今朝|聽日|听日|聽朝|聽晚|明日|明天|明晚|明早|後日|后日|後天|大後日"
        r"|尋日|琴日|昨日|昨天|今個禮拜|下個禮拜|下禮拜|下星期|下週|下周|呢個週末|週末|周末"
        r"|晏晝|下晝|朝早|今個月|下個月",
        r"(?:禮拜|星期|週|周)[一二三四五六日天]",
        # 7點, 七點半, 7點15, 3點鐘, 幾點, 8時
        _CN_NUM + r"+\s*[點点](?:半|鐘|钟|" + _CN_NUM + r"+(?:分|個字)?)?",
        r"幾[點点]",
        r"(?<!\d)\d{1,2}\s*[時时]",
        # 9月28號, 28號, 28日 (a bare 號 is also a bus number: accepted)
        _CN_NUM + r"{1,2}\s*月\s*" + _CN_NUM + r"{1,3}\s*[號号日]?",
        r"(?<!\d)\d{1,2}\s*[號号]",
    )),
    re.IGNORECASE,
)

_AMOUNT = re.compile(
    "|".join((
        r"(?:hk)?\$\s?\d",
        r"\d+(?:\.\d+)?\s*(?:hkd|dollars?|bucks)(?![a-z])",
        r"\d+(?:\.\d+)?\s*(?:蚊|元|文)",
        r"hkd\s?\d",
        r"\bsplit\b|\bper\s+(?:person|head|pax)\b|\bgo\s+dutch\b|\bowes?\b|\bAA\b",
        r"夾錢|夾份|每人|每位|人頭|分單|分帳|分账|AA制|找數|欠你|欠我|畀錢|俾錢|轉數",
    )),
    re.IGNORECASE,
)

_KEEP = re.compile(
    "|".join((
        r"記低|記錄低|记低",
        # 記住 as an ask (記住呢個, 記住: …, Nora記住), never «記住啦你» banter,
        # «唔記住» or «記住咗» (DEC-306).
        r"(?<!唔)記住(?!啦你|囉你|呀你|咗|嚟)",
        r"\bkeep\s+(?:that|this)\b",
        # «remember this», or «remember that» closing a clause; never «remember
        # that time we …», which is reminiscing.
        r"\bremember\s+this\b|\bremember\s+that\s*(?:[.!:,;]|$)",
        r"\b(?:write|note|jot)\s+(?:that|this|it)\s+down\b",
        r"\b(?:write|note|jot)\s+down\s+(?:that|this)\b",
    )),
    re.IGNORECASE,
)

_RSVP = re.compile(
    "|".join((
        r"\bi'?m\s+(?:in|out|going|coming)\b|\bim\s+(?:in|out|going|coming)\b",
        r"\bcount\s+me\s+(?:in|out)\b",
        r"\bi'?ll\s+(?:come|be\s+there|join)\b",
        r"\b(?:can'?t|cannot|can\s+not)\s+make\s+it\b|\bcan\s+make\s+it\b",
        r"\bnot\s+(?:going|coming)\b",
        r"(?:^|\s)\+1(?:\s|$)",
        r"我去|我都去|我嚟|我都嚟|我來|去唔到|嚟唔到|唔去|唔嚟|得閒|唔得閒|算我一份|報名|出席|缺席",
    )),
    re.IGNORECASE,
)

_PLACE_LINK = re.compile(
    r"https?://(?:"
    r"maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.)?google\.[a-z.]+/maps|maps\.google\.[a-z.]+"
    r"|maps\.apple\.com|(?:www\.)?openrice\.com|g\.co/kgs|(?:www\.)?waze\.com"
    r"|(?:www\.)?instagram\.com/explore/locations"
    r")",
    re.IGNORECASE,
)

_SURPRISE = re.compile(
    "|".join((
        r"\b(?:don'?t|do\s+not|dont)\s+tell\b",
        r"\bkeep\s+it\s+(?:a\s+)?secret\b",
        r"\bsurprise\b",
        r"唔好(?:話|讲|講|同)[^。!?\n]{0,8}(?:知|講|讲|聽)",
        r"秘密|驚喜|惊喜|保密",
    )),
    re.IGNORECASE,
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("datetime", _DATETIME),
    ("amount", _AMOUNT),
    ("keep", _KEEP),
    ("rsvp", _RSVP),
    ("place_link", _PLACE_LINK),
    ("surprise", _SURPRISE),
)


def cues_of(text: str | None) -> tuple[str, ...]:
    """The cues `text` carries, in `CUES` order, each at most once.

    NFKC first, so a full-width digit or colon reads as its ASCII form (a
    phone's IME emits both). Total: `None` or `""` carries no cue."""
    if not text:
        return ()
    folded = unicodedata.normalize("NFKC", text)
    return tuple(name for name, pattern in _PATTERNS if pattern.search(folded))


def is_strong(cues: tuple[str, ...] | frozenset[str]) -> bool:
    """Whether any of `cues` trips the strong-cue trigger."""
    return any(c in STRONG_CUES for c in cues)


def is_rearming(cues: tuple[str, ...] | frozenset[str]) -> bool:
    """Whether any of `cues` trips the strong-cue trigger straight after a
    `strong_cue` wake (`REARMING_CUES`)."""
    return any(c in REARMING_CUES for c in cues)
