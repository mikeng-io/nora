"""R-85 (DEC-148): refuse an outbound reply that puts words in the wrong
mouth — the send-path guard the 2026-08-28 incident had none of.

**What shipped that day did not compel anything.** `conduct.md` gained a
grounding rubric (R-81/DEC-145), `who_said` gave her a tool to check before
attributing (DEC-146), and the debrief learned to score a misattribution as
ungrounded rather than merely wrong (R-83/DEC-147). All three need her to
CHOOSE to use them — a doctrine instruction is a request to a model, and
`nora.agent.egress`'s own docstring already names this codebase as bitten four
times by things true in intent and false in fact. This module is the one
piece that does not ask: it sits on the send path itself, mechanically, and a
reply this refuses never reaches the group regardless of what she meant.

**The property, exactly**: a reply that QUOTES a string — delimited the way
this group actually writes reported speech, corner brackets, 「like this」 —
is refused when the current window shows that string being said, and said
ONLY by somebody other than the person she is addressing. Two failure modes
this does NOT try to catch, on purpose, because catching them mechanically
would cost real replies to buy nothing real:

* **A quote nobody in the window said at all.** Pure fabrication is a
  different defect (an ungrounded claim, R-81's lane) and this module cannot
  tell "invented" from "said further back than the window reaches" — both
  read as "not found here", and refusing on that would block every accurate
  reference to something outside the last ~40 messages, which is most of what
  a running conversation is.
* **A quote the addressee ALSO said**, anywhere in the window, alongside
  somebody else. If the person being spoken to is in the speaker set, the
  quote is fair game for her to lay at his door — the incident's own 「聽晚機
  會係0」「大到唔出」 clauses are exactly this shape, and a predicate that
  fired on them too would have refused the two accurate quotes along with the
  two wrong ones.

**Mechanical only** (R-2 of the brief): substring matching against
`TagContext.transcript`, the same tuple `who_said` and `<group_transcript>`
already comprehend once from. No model call, no paraphrase understanding, no
judgement about whether the ATTITUDE in the reply is fair — only whether the
literal quoted string traces to the right mouth.

**"The person being addressed", decided and defended.** There is no field
anywhere in this codebase that names who a reply is FOR — `send_reply` takes
one string, full stop (DEC-24's own argument against inventing parameters).
Three signals exist, in descending order of how explicit they are, and the
incident is the case where none of the explicit ones fire:

1. **An `@Name` written in THIS reply**, matched only against people who are
   actually in the window (`send_reply`'s own docstring: "@ immediately
   before the name... addresses them"). The most explicit signal there is —
   she said, in the message itself, who she means — so it is checked first
   and it is additive: naming someone by @ makes THEM fair game to quote back
   at, on top of whoever the fallback below already allows.
2. **The tagger — whoever's message brought about this turn.** This is the
   fallback the incident needs, and the one worth defending on its own:
   absent an explicit `@`, the person Nora is speaking to in an ordinary
   chat reply is whoever she is replying TO, and that is exactly what
   `TagContext.tagger` already is — comprehended once at context assembly,
   never re-derived here. In the incident this is Avery: his flat denial is
   the message the refused reply answers, and he is who "你" refers to in
   「你逼人逼到收埋自己」. A quote-of-the-triggering-message
   resolution (whoever the tagger's OWN message quoted) was considered and
   dropped — the tagger is still who Nora is talking to even when his
   message was itself a reply to a third person, so that signal would have
   added a second candidate with no clearer claim to being "the addressee"
   than the tagger already has, for no case this repo's incident needed.
3. **Nobody resolvable at all** — an unmapped sender, or a fire-path occasion
   with no `person_id` — and here the design commits to NOT refusing. A guard
   that blocks because it cannot tell who is being spoken to is answering a
   question it was not asked; the brief's own instruction is that a false
   refusal is worse than the bug being fixed, and "cannot resolve an
   addressee" is a `nothing_found`-shaped absence, not grounds for `unavailable`
   escalating into a block. This is the one place this module fails OPEN
   rather than closed, and it is deliberate: R-27's fail-closed discipline is
   about a CLAIM Nora makes ("nobody said that"), not about a guard's own
   epistemic state licensing it to silence her.

Nora's own rows (`is_nora`) are excluded from the speaker search entirely —
quoting her own earlier words back is never a misattribution, whoever she is
speaking to, and leaving her rows in the candidate pool would flag exactly
that ordinary case.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..domain.models import TranscriptRow

# 「...」 — the corner brackets this group actually uses for reported speech,
# and the delimiter the incident's own reply used four times over. Straight
# quotes and full-width curly quotes are deliberately NOT matched: an
# apostrophe inside an English contraction or a possessive would open a false
# span on ordinary prose, and this repo's transcripts are overwhelmingly
# Cantonese, where 「」 is the unambiguous quoting convention — extending the
# pattern is a change to make WITH a false-refusal measurement in hand, not
# ahead of one.
_QUOTE_RE = re.compile(r"「([^」]+)」")

# A span shorter than this, once whitespace-collapsed, is skipped. A
# one-character "quote" — 「呀」, 「喎」 — is a filler particle any number of
# people say in one window; matching it against a single speaker set is
# indistinguishable from noise, and the false-refusal measurement this rule
# shipped with (see the fragment / DEC-148 row) is what actually calibrated
# this number rather than a guess.
MIN_QUOTE_CHARS = 2


def _normalise(text: str | None) -> str:
    """Whitespace-collapsed, casefolded — identical to `nora.agent.tools._said`
    on purpose (DEC-146's own matching pass), so a line the prefill rendered
    across a wrapped newline still matches the phrase a quote copied off it.
    Not imported from there: that module's `_said` is private to `who_said`'s
    tool closure, and duplicating one normalisation line costs far less than
    reaching into another module's underscore-prefixed internals for it."""
    return " ".join((text or "").split()).casefold()


def addressee_candidates(
    *,
    tagger_person_id: str | None,
    transcript: tuple[TranscriptRow, ...],
    reply_text: str,
) -> frozenset[str]:
    """Who this reply may fairly quote without it reading as misattribution.

    See the module docstring for the three-signal design this implements:
    the tagger, plus anyone explicitly `@`-named in `reply_text` who is
    actually a participant in `transcript`. Keyed on `person_id` throughout —
    the roster's own canonical id, and the same key `TagContext.tagger`
    already carries — so a person who holds two accounts is one candidate,
    not two.

    Empty means "cannot resolve who this is for", which
    `find_misattributed_quote` treats as licence to check nothing, never as
    licence to block everything (the module docstring's third signal).
    """
    candidates: set[str] = set()
    if tagger_person_id:
        candidates.add(tagger_person_id)
    lowered = reply_text.casefold()
    for row in transcript:
        if row.is_nora or not row.person_id:
            continue
        name = (row.person_name or row.sender_name or "").strip()
        if len(name) < 2:
            continue
        if f"@{name.casefold()}" in lowered:
            candidates.add(row.person_id)
    return frozenset(candidates)


def _speakers_of(needle: str, transcript: Iterable[TranscriptRow]) -> set[str]:
    """Every `person_id` (falling back to `sender_id` for an unmapped sender)
    whose row contains `needle` — HUMAN rows only, per the module docstring's
    self-quote carve-out."""
    return {
        (row.person_id or row.sender_id)
        for row in transcript
        if not row.is_nora and needle in _normalise(row.body)
    }


def find_misattributed_quote(
    text: str,
    *,
    transcript: tuple[TranscriptRow, ...],
    addressee: frozenset[str],
) -> str | None:
    """The first 「quoted」 span in `text` the window shows came ONLY from
    somebody outside `addressee` — or `None` if every span is either unfound
    in the window (see the module docstring for why that is not this
    predicate's job) or said by the person it is aimed at.

    Mechanical: two nested substring passes over data the turn already holds,
    no model call and no call to anything that could fail. `addressee` empty
    is "cannot judge" (see `addressee_candidates`), so it returns `None`
    unconditionally rather than treating an unresolved addressee as
    everyone-is-suspect.
    """
    if not addressee:
        return None
    for quote in _QUOTE_RE.findall(text):
        needle = _normalise(quote)
        if len(needle) < MIN_QUOTE_CHARS:
            continue
        speakers = _speakers_of(needle, transcript)
        if speakers and speakers.isdisjoint(addressee):
            return quote
    return None
