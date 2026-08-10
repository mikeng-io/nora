"""The WIRE FORM of one outbound message: her text, plus what rides beside it.

This module answers one question — *given the exact string that is about to be
sent, what decoration goes with it* — and it is the ONLY place allowed to
answer it. Two decorations today: @-mentions (a range over the text plus an id
that is never in the text) and a link preview card.

WHY IT IS A SEPARATE STAGE, AND WHY IT RUNS LAST
------------------------------------------------
A mention is `(start, length, recipient)` over the message body. Those offsets
are only meaningful against ONE string — the exact bytes the platform receives
— so every transformation of the text has to happen BEFORE they are computed,
and nothing may touch the text after. That is not a style preference; it is the
one way this feature breaks silently. A later stage that trims, reflows, strips
markdown, appends a footer or normalises whitespace shifts every offset after
the edit, and Signal does not reject the result: it renders somebody's name
over the wrong span of somebody else's sentence, in a group, with no error
anywhere.

The structural answer is that `compose` OWNS the final text. It returns an
`Outbound` whose `.text` is what `Deliverer` hands to `adapter.send`, so:

* a text transformation added later belongs INSIDE `compose`, ahead of the
  offset arithmetic, where it is correct by construction;
* a text transformation added AFTER the `compose` call in `Deliverer.deliver`
  is INERT — it cannot reach the wire, because the wire reads `outbound.text`
  and not the local it would have rewritten. Wrong-but-inert beats
  correct-looking-but-corrupting: the missing transformation is visible in the
  group as unstripped text, whereas a shifted mention is invisible to everyone
  except the person wrongly tagged.

`tests/architecture/test_outbound_offsets_are_computed_last.py` is what keeps
that true — it asserts, on the AST, that the value `Deliverer.deliver` passes
to `adapter.send(text=...)` is an attribute of the compose result and never a
bare local.

WHY THE MODEL DOES NOT GET A `mentions` PARAMETER
-------------------------------------------------
`send_reply(text)` is unchanged, and the mention is written the way a person
writes one: 「@grant 你幾高?」. Three reasons, in order of weight:

1. **A second parameter is a second place for the model to be wrong.** A
   `mentions=["grant"]` argument that disagrees with the text (named someone
   the sentence never mentions, or spelled them differently) has no correct
   resolution, and the model would have to keep two representations of one fact
   in agreement across a turn. The text IS the fact.
2. **Every id form stays out of the model's reach.** The model never names a
   recipient; it names a person, in the way it already refers to people in
   prose, and the id is looked up here. There is no argument shape in which it
   could supply an ACI even if a prompt-injected turn tried.
3. It is what the operator asked for, verbatim — 「我唔知你幾高」 then
   「@grant 你幾高?」 as two messages, which `send_reply`'s budget of 3
   (DEC-42) already permits with no budget change.

The cost is a parser, and the parser is deliberately NOT "a regex for @word".
It matches the LONGEST handle in a KNOWN VOCABULARY — every name, first name
and alias in `identity.toml` — so there is no guess about where a handle ends,
which matters immediately in this group: 「@阿豹同我講」 has no whitespace and
no punctuation to split on, and a `[\\w]+` run would swallow the sentence.

FAIL-CLOSED, AND WHAT THAT MEANS HERE
--------------------------------------
The decision the whole module turns on: **an unresolvable mention drops to
plain text; it never sends a range, and it never refuses the message.**

* Never a range, because a mention Signal cannot route is worse than no
  mention. `SendCommand` throws `UserErrorException` on a recipient string it
  cannot parse, which fails the WHOLE send — so a guessed id does not degrade
  to a cosmetic miss, it costs the message (R-5's exact shape: the group hears
  nothing and nothing looks broken). And a range that parses but names the
  wrong account tags a real person into a conversation about somebody else.
* Never a refusal, because the mention is decoration on a sentence that
  answers a human. R-5 says silence beats a wrong answer; it does not say
  silence beats an untagged answer. 「@grant 你幾高?」 sent as plain text is
  the same question, minus a notification.

So this is fail-closed on the RANGE and fail-open on the MESSAGE, and the two
are not in tension — the closed half is what makes the open half safe.

**The roster gate** is the sharp end of that. A handle resolving through
`identity.toml` is not enough to tag someone: `identity.toml` is Nora's address
book across every room she is in, and it is loaded once at startup, so it can
be stale and it is certainly wider than this conversation. The id that goes on
the wire has to be one the PLATFORM itself just listed as a member of THIS
group — `RosterSource.roster`, intersected per-account. Three consequences,
all deliberate:

* a person who is in `identity.toml` but not in this room is not tagged
  (`not_in_room`) — the same resolve-WITHIN-scope rule `nora.agent.room`'s
  docstring argues for, applied to the outbound direction;
* a roster that could not be read tags NOBODY (`roster_unavailable`), because
  «I could not check who is here» is not «they are here» (R-22, R-27);
* the id sent is picked out of the INTERSECTION, so it is an id form
  signal-cli emitted a moment ago rather than one a config file claims.

The roster read is lazy and cached for the composer's life (one turn): a reply
with no resolvable handle never touches the daemon, so this costs nothing on
the overwhelmingly common message.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..domain.ports import LinkPreview, LinkPreviewSource, Mention, Roster, RosterSource
from ..persons import Persons
from .egress import scan_identifiers

log = logging.getLogger("nora.agent.outbound")

# The longest handle this will consider, in characters. Not a safety bound —
# the vocabulary is operator-authored and closed — but a bound on the work done
# per `@` in a message, so a reply full of stray at-signs cannot turn into a
# quadratic scan of every alias in the file.
MAX_HANDLE_CHARS = 48

# A URL in her own text, for the preview card. Deliberately narrow: `http(s)`
# only (a `data:`/`file:` URL is not something a preview should ever chase),
# and stopping at whitespace or a bracket. The trailing-punctuation strip below
# is what makes 「睇下 https://n.example.net/abc。」 yield the link and not the
# sentence — and every character it strips leaves the result still a SUBSTRING
# of the body, which signal-cli requires ("The same url must also appear in the
# message body, otherwise the preview won't be displayed by the apps").
_URL = re.compile(r"https?://[^\s<>\"'　]+")

# Trailing characters that end a sentence rather than a URL, ASCII and CJK
# both. `)` is included even though a URL may legitimately contain one: a
# preview that misses is cosmetic, a preview whose url is not a substring of
# the body renders nothing at all, and neither is worth the ambiguity.
_URL_TAIL = "。，、！？：；)）]】>》.,!?:;'\""  # noqa: RUF001 - CJK punctuation is the point


def utf16_len(text: str) -> int:
    """Length of `text` in UTF-16 code units — the unit Signal's ranges use.

    NOT `len(text)`, and the difference is not theoretical in this group: 「幾」
    is one Python character and one UTF-16 unit, 「🇭🇰」 is two Python
    characters and FOUR UTF-16 units (two surrogate pairs), and 「😅」 — which
    is in the never-silent fallback line this repo already ships — is one
    Python character and TWO. signal-cli says the unit twice in its own man
    page (`--mention` and `--text-style`) and links its FAQ's string-indexing
    note; getting it wrong shifts every mention after the first emoji.

    Computed by encoding rather than by counting `ord(c) > 0xFFFF`, because the
    encoder is the definition and a hand-rolled surrogate count is a second
    implementation of it that can drift. `utf-16-le` (not `utf-16`) because the
    plain codec prepends a two-byte BOM, which would add one unit to every
    answer.
    """
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class Outbound:
    """One message as it goes on the wire: the FINAL text, plus its decoration.

    Frozen, and `text` is the authoritative copy — see this module's docstring
    for why that is the mechanism and not a detail. Constructing one by hand
    outside `OutboundComposer.compose` is how the offsets and the text get to
    disagree, so the only public producer is that method.
    """

    text: str
    mentions: tuple[Mention, ...] = ()
    preview: LinkPreview | None = None


def _is_word(ch: str) -> bool:
    """ASCII word character — the only alphabet a handle boundary is asserted in.

    Boundary checking is done ONLY between two ASCII word characters, and that
    asymmetry is the point. `@miles` inside `@mikeson` must not match when only
    `miles` is a known handle (ASCII on both sides — reject), while 「@阿豹同我講」
    must match the handle 「阿豹」 (CJK on both sides — accept), because Chinese
    is written without separators and demanding one would make every CJK handle
    unmatchable in an ordinary sentence. `str.isalnum()` is true for 同 as well
    as for `s`, so a boundary rule expressed in `isalnum()` alone would silently
    be the strict one everywhere.
    """
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _preferred_recipient(ids: frozenset[str]) -> str | None:
    """The id form to put on the wire, out of the ones the ROSTER confirmed.

    ACI first. Under sealed sender the same account reaches Nora as a phone
    number on one message and as an ACI on another (design/10 §1.1), the ACI is
    the stable one, and it is the form `nora.platform.signal.react` already
    sends as `targetAuthor` for exactly this reason.

    Anything that is neither a UUID nor an E.164 number returns `None` rather
    than being sent hopefully. A Signal USERNAME is the concrete case: it is a
    valid `identity.toml` id kind, but signal-cli's RECIPIENT grammar needs it
    prefixed `u:` and a bare one would be parsed as a phone number and rejected
    — failing the whole send (see this module's docstring). The roster gate
    makes this branch nearly unreachable on its own (`listGroups` reports
    numbers and ACIs), which is why it is a `None` and not a prefix hack: there
    is no live case to validate a hack against.
    """
    uuids = sorted(v for v in ids if _UUID_RE.fullmatch(v))
    if uuids:
        return uuids[0]
    numbers = sorted(v for v in ids if _E164_RE.fullmatch(v))
    return numbers[0] if numbers else None


# Deliberately re-stated here rather than imported from `nora.agent.egress`:
# those two patterns exist to REFUSE a string, and are tuned to fire on a
# substring anywhere in a sentence (`\b`-anchored, `+`-required). These two
# have the opposite job — deciding that a WHOLE id is of a known form before it
# is sent — so they are `fullmatch`-shaped. Sharing one pattern between "block
# if this appears anywhere" and "accept only if this is the entire value" is
# how one of the two silently loosens when the other is tuned.
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_E164_RE = re.compile(r"\+\d{8,15}")


def first_url(text: str) -> str | None:
    """The first http(s) URL in `text`, as a substring of `text`, or None.

    A SUBSTRING is the contract, not merely a URL: signal-cli requires the
    preview's url to appear in the message body verbatim or the apps show no
    card at all, so a normalised, unescaped or redirect-followed URL here would
    produce a message that carries a preview payload nobody ever sees.
    """
    match = _URL.search(text)
    if match is None:
        return None
    url = match.group(0).rstrip(_URL_TAIL)
    return url or None


class OutboundComposer:
    """Turns one final reply string into its `Outbound`. Constructed per turn.

    Per turn, and not per process, because the roster cache below is scoped to
    one conversation and one moment — the same reason `nora.agent.room.Room` is
    per tag. Collaborators arrive through `__init__` only (CLAUDE.md's
    test-seam policy); there is no module-level state and nothing here is
    reused between turns.

    `roster_source` and `preview_source` are BOTH optional, and they degrade
    differently on purpose:

    * no `roster_source` → no mention can be confirmed against the room, so
      NOTHING is tagged. The strict direction, and it is what the offline and
      eval compositions get for free.
    * no `preview_source` → no card. `[preview].enabled = false` is the shipped
      default, so this is the ordinary state, not a degradation.
    """

    def __init__(
        self,
        *,
        platform: str,
        conversation_id: str,
        persons: Persons,
        metrics: Any,
        roster_source: RosterSource | None = None,
        preview_source: LinkPreviewSource | None = None,
    ) -> None:
        self._platform = platform
        self._conversation_id = conversation_id
        self._persons = persons
        self._metrics = metrics
        self._roster_source = roster_source
        self._preview_source = preview_source
        self._handles = _handle_index(persons)
        self._roster: Roster | None = None

    async def compose(self, text: str) -> Outbound:
        """`text` plus its decoration. Never raises; never returns other text.

        **`text` is returned unchanged today, and that is a fact about today,
        not a promise.** If a transformation of the outbound string is ever
        needed — stripping markdown the group's clients render as literal
        asterisks is the obvious candidate — it belongs on the first line of
        this method, ahead of every offset. See the module docstring.
        """
        mentions = await self._mentions(text)
        preview = await self._preview(text)
        return Outbound(text=text, mentions=mentions, preview=preview)

    # ------------------------------------------------------------------ mentions

    async def _mentions(self, text: str) -> tuple[Mention, ...]:
        found = self._scan(text)
        if not found:
            return ()                    # the common message: no roster read
        out: list[Mention] = []
        for start_char, handle_len, person_id in found:
            recipient = await self._recipient(person_id)
            if recipient is None:
                continue                 # counted at the point of refusal
            out.append(Mention(
                start=utf16_len(text[:start_char]),
                # `1 +` is the `@` itself: the range Signal replaces is the
                # whole handle AS WRITTEN, sigil included, or the rendered chip
                # lands next to an orphan `@`.
                length=utf16_len(text[start_char:start_char + 1 + handle_len]),
                recipient=recipient,
            ))
            self._metrics.inc("nora_outbound_mentions_total", outcome="sent")
        return tuple(out)

    def _scan(self, text: str) -> list[tuple[int, int, str]]:
        """Every `@handle` in `text` as `(index of @, handle length, person_id)`.

        Longest-match over the known vocabulary, left to right, non-overlapping.
        Longest FIRST is what makes 「@阿豹妹」 resolve to 阿豹妹 rather than to
        阿豹 with a stray 妹 — a shorter handle that is a prefix of a longer one
        is the normal case in a family group, not an edge case.
        """
        out: list[tuple[int, int, str]] = []
        i = 0
        while (i := text.find("@", i)) != -1:
            if i > 0 and (_is_word(text[i - 1]) or text[i - 1] == "@"):
                # `a@b.com`, and a doubled `@@`. An `@` glued to a word is an
                # address or a handle from somewhere else, never a mention.
                i += 1
                continue
            match = self._match_handle(text, i + 1)
            if match is None:
                i += 1
                continue
            handle, person_id = match
            out.append((i, len(handle), person_id))
            i += 1 + len(handle)
        return out

    def _match_handle(self, text: str, at: int) -> tuple[str, str] | None:
        window = text[at:at + MAX_HANDLE_CHARS].casefold()
        for handle in self._handles:            # pre-sorted longest first
            if not window.startswith(handle):
                continue
            end = at + len(handle)
            if end < len(text) and _is_word(handle[-1]) and _is_word(text[end]):
                continue                        # `@miles` inside `@mikeson`
            person_id = self._handles[handle]
            if person_id is None:
                # An AMBIGUOUS handle — two people answer to it. Refused rather
                # than resolved to either: tagging the wrong member of a family
                # group is a worse outcome than tagging nobody, and there is no
                # tie-break available that is not a guess.
                self._metrics.inc("nora_outbound_mentions_total", outcome="ambiguous")
                return None
            return handle, person_id
        return None

    async def _recipient(self, person_id: str) -> str | None:
        ids = self._persons.platform_ids(self._platform, person_id)
        if not ids:
            self._metrics.inc("nora_outbound_mentions_total", outcome="unresolved")
            return None
        roster = await self._read_roster()
        if not roster.available:
            self._metrics.inc(
                "nora_outbound_mentions_total", outcome="roster_unavailable",
            )
            return None
        for member in roster.members:
            shared = ids & member
            if not shared:
                continue
            recipient = _preferred_recipient(shared)
            if recipient is None:
                # In the room, but every id form the platform gave for them is
                # one signal-cli's RECIPIENT grammar would not take. Counted
                # under `unresolved` rather than a fifth label: the operator's
                # action is the same as for an unmapped person (fix
                # identity.toml), and `rule`/`outcome` values are as
                # append-only as the metric names (DEC-33).
                self._metrics.inc("nora_outbound_mentions_total", outcome="unresolved")
            return recipient
        self._metrics.inc("nora_outbound_mentions_total", outcome="not_in_room")
        return None

    async def _read_roster(self) -> Roster:
        """The room's membership, read at most once per composer.

        `RosterSource.roster` never raises by contract; the guard is here
        anyway because this call sits between a human's question and her
        answer, and a port whose implementation broke that contract would
        otherwise take the whole message down for a decoration.
        """
        if self._roster is not None:
            return self._roster
        if self._roster_source is None:
            self._roster = Roster(available=False)
            return self._roster
        try:
            self._roster = await self._roster_source.roster(
                conversation_id=self._conversation_id,
            )
        except Exception:                       # a mention is decoration
            log.exception("roster read raised while composing a mention")
            self._roster = Roster(available=False)
        return self._roster

    # ------------------------------------------------------------------- preview

    async def _preview(self, text: str) -> LinkPreview | None:
        if self._preview_source is None:
            return None
        url = first_url(text)
        if url is None:
            return None
        try:
            preview = await self._preview_source.preview_for(url=url)
        except Exception:                       # a card is decoration
            log.exception("preview source raised; sending without a card")
            self._metrics.inc("nora_link_previews_total", outcome="unavailable")
            return None
        if preview is None:
            self._metrics.inc("nora_link_previews_total", outcome="unavailable")
            return None
        # R-18 on somebody ELSE's text. `scan_identifiers`, NOT `scan_reply`,
        # and the distinction is `nora.agent.egress`'s own (DEC-30): the
        # identifier floor applies to everything that leaves, including a
        # fetched page's title, because a number rendered in a card is in the
        # room exactly as much as one she typed. The model-name half does not,
        # because that rule is «she does not SAY what she runs on» and a third
        # party's page title is not her saying anything — screening it here
        # would silently drop the preview of any page about LLMs, which in this
        # group is a page they are likely to be discussing.
        kind = scan_identifiers(f"{preview.title} {preview.description}")
        if kind:
            log.error("preview_blocked: %s in the fetched card for %s", kind, preview.url)
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            self._metrics.inc("nora_link_previews_total", outcome="blocked")
            return None
        self._metrics.inc("nora_link_previews_total", outcome="attached")
        return preview


def _handle_index(persons: Persons) -> dict[str, str | None]:
    """Every name Nora may be tagged with, longest first, `None` if ambiguous.

    Built from `name`, `first_name` and `aliases` — the SAME three fields
    `nora.agent.room.RoomView.find` matches a lookup target on, and safe here
    for the same reason it is safe there (`nora.agent.room`'s docstring, §alias
    matching): design/10 §1.3 forbids an alias resolving a SENDER, because a
    display name is spoofable and matching one would hand identity to whoever
    can type it. This is not a sender question. The names are operator-authored
    in `identity.toml`, the text being matched is Nora's OWN, and the answer is
    then narrowed to the room's actual members before anything is sent.

    A handle claimed by two people maps to `None` rather than to one of them —
    see `_match_handle`. The dict is insertion-ordered longest-first so
    `_match_handle` can return on its first hit.
    """
    claims: dict[str, set[str]] = {}
    for person_id, ref in persons.by_person.items():
        for raw in (ref.name, ref.first_name, *ref.aliases):
            handle = (raw or "").strip().casefold()
            if not handle or len(handle) > MAX_HANDLE_CHARS:
                continue
            claims.setdefault(handle, set()).add(person_id)
    ordered = sorted(claims, key=len, reverse=True)
    return {
        handle: (next(iter(claims[handle])) if len(claims[handle]) == 1 else None)
        for handle in ordered
    }
