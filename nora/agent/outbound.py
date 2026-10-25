"""The WIRE FORM of one outbound message: her text, plus what rides beside it.

This module answers one question, *given the exact string that is about to be
sent, what decoration goes with it*, and it is the ONLY place allowed to answer
it. Three decorations: @-mentions (a range over the text plus an id that is never
in the text), text styles (a range plus BOLD or ITALIC), and a link preview card.

Why it is a separate stage, and why it runs last
------------------------------------------------
A mention is `(start, length, recipient)` over the message body. Those offsets
are only meaningful against ONE string, the exact bytes the platform receives, so
every transformation of the text has to happen BEFORE they are computed, and
nothing may touch the text after. A later stage that trims, reflows, rewrites
markdown, appends a footer or normalises whitespace shifts every offset after the
edit, and Signal does not reject the result: it renders somebody's name over the
wrong span of somebody else's sentence, in a group, with no error anywhere.

The structural answer is that `compose` OWNS the final text. It returns an
`Outbound` whose `.text` is what `Deliverer` hands to `adapter.send`, so a text
transformation added AFTER the `compose` call in `Deliverer.deliver` is INERT: it
cannot reach the wire, because the wire reads `outbound.text` and not the local
it would have rewritten. Wrong-but-inert beats correct-looking-but-corrupting:
the missing transformation is visible in the group as raw markdown, whereas a
shifted mention is invisible to everyone except the person wrongly tagged.

`compose` takes a `Styled`, not a string: the final text and the style ranges
over it, as one frozen value produced by `nora.agent.markdown.render_markdown`.

* The pre-conversion draft is not in this method's scope, so it cannot be handed
  to the mention scanner by mistake. The mention offsets and the style offsets
  index the same string because there is only one string here to index.
* `Outbound` STORES that `Styled` and exposes `.text`/`.styles` as properties of
  it, so a message whose styles index a different string than its text is
  unconstructible rather than merely untested.

The markdown conversion therefore happens in `Deliverer.deliver`, one line before
the guards, not on the first line of `compose`. R-6/R-18 require a fail-closed
guard to see the CONVERTED text as well as the drafted one (`"*+85298765432*a"`
scans clean once the asterisks are gone), so `deliver` holds the converted form
before it composes. The transformation still precedes every offset, and
`compose` still owns the final text; it receives it already final.

`tests/architecture/test_outbound_offsets_are_computed_last.py` asserts, on the
AST, that the value `Deliverer.deliver` passes to `adapter.send(text=...)` is an
attribute of the compose result and never a bare local, that the guards measure
the same value that is composed, and that `compose` reads nothing but its
`Styled` parameter.

Why the model does not get a `mentions` parameter
-------------------------------------------------
`send_reply(text)` takes no mentions argument; the mention is written the way a
person writes one: 「@grant 你幾高?」. Reasons, in order of weight:

1. **A second parameter is a second place for the model to be wrong.** A
   `mentions=["grant"]` argument that disagrees with the text (named someone the
   sentence never mentions, or spelled them differently) has no correct
   resolution. The text IS the fact.
2. **Every id form stays out of the model's reach.** The model names a person, in
   the way it already refers to people in prose, and the id is looked up here.
   There is no argument shape in which it could supply an ACI even if a
   prompt-injected turn tried.
3. Two messages (「我唔知你幾高」 then 「@grant 你幾高?」) are already permitted by
   `send_reply`'s budget of 3 (DEC-42).

The parser is deliberately NOT "a regex for @word". It matches the LONGEST handle
in a KNOWN VOCABULARY (every name, first name and alias in `identity.toml`), so
there is no guess about where a handle ends. That matters for 「@阿豹同我講」,
which has no whitespace or punctuation to split on, where a `[\\w]+` run would
swallow the sentence.

Fail-closed, and what that means here
-------------------------------------
**An unresolvable mention drops to plain text; it never sends a range, and it
never refuses the message.**

* Never a range, because a mention Signal cannot route is worse than no mention.
  `SendCommand` throws `UserErrorException` on a recipient string it cannot parse,
  which fails the WHOLE send, so a guessed id costs the message (R-5's shape: the
  group hears nothing and nothing looks broken). And a range that parses but names
  the wrong account tags a real person into a conversation about somebody else.
* Never a refusal, because the mention is decoration on a sentence that answers a
  human. R-5 says silence beats a wrong answer; it does not say silence beats an
  untagged answer. 「@grant 你幾高?」 sent as plain text is the same question,
  minus a notification.

So this is fail-closed on the RANGE and fail-open on the MESSAGE; the closed half
is what makes the open half safe.

**The roster gate.** A handle resolving through `identity.toml` is not enough to
tag someone: `identity.toml` is Nora's address book across every room she is in,
loaded once at startup, so it can be stale and it is wider than this
conversation. The id that goes on the wire has to be one the PLATFORM itself just
listed as a member of THIS group (`RosterSource.roster`, intersected
per-account):

* a person who is in `identity.toml` but not in this room is not tagged
  (`not_in_room`), the same resolve-WITHIN-scope rule `nora.agent.room` applies,
  in the outbound direction;
* a roster that could not be read tags NOBODY (`roster_unavailable`), because
  «I could not check who is here» is not «they are here» (R-22, R-27);
* the id sent is picked out of the INTERSECTION, so it is an id form the platform
  reported rather than one a config file claims.

The roster read is lazy and cached for the composer's life (one turn): a reply
with no resolvable handle never touches the daemon.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..domain.ports import (
    LinkPreview,
    LinkPreviewSource,
    Mention,
    Roster,
    RosterSource,
    Styled,
    TextStyle,
)
from ..domain.utf16 import utf16_len
from ..persons import Persons
from .egress import scan_identifiers

log = logging.getLogger("nora.agent.outbound")

# The longest handle this will consider, in characters. Not a safety bound (the
# vocabulary is operator-authored and closed) but a bound on the work done per
# `@` in a message, so a reply full of stray at-signs cannot turn into a
# quadratic scan of every alias in the file.
MAX_HANDLE_CHARS = 48

# A URL in her own text, for the preview card. Deliberately narrow: `http(s)`
# only (a `data:`/`file:` URL is not something a preview should ever chase),
# and stopping at whitespace or a bracket. The trailing-punctuation strip below
# makes 「睇下 https://example.net/abc。」 yield the link and not the sentence,
# and every character it strips leaves the result a SUBSTRING of the body, which
# signal-cli requires ("The same url must also appear in the message body,
# otherwise the preview won't be displayed by the apps").
_URL = re.compile(r"https?://[^\s<>\"'　]+")

# Trailing characters that end a sentence rather than a URL, ASCII and CJK
# both. `)` is included even though a URL may legitimately contain one: a
# preview that misses is cosmetic, a preview whose url is not a substring of
# the body renders nothing at all.
_URL_TAIL = "。，、！？：；)）]】>》.,!?:;'\""  # noqa: RUF001 - CJK punctuation is the point


@dataclass(frozen=True)
class Outbound:
    """One message as it goes on the wire: the FINAL text, plus its decoration.

    Frozen, and `styled` is the authoritative copy of the text (see this module's
    docstring). Constructing one by hand outside `OutboundComposer.compose` is how
    the offsets and the text get to disagree, so the only public producer is that
    method.

    **`text` and `styles` are properties, not fields.** They read one `Styled`,
    so no assignment can pair this message's text with another message's ranges:
    the correspondence is held by object identity rather than by a convention.
    `Mention` ranges are a separate field because they are computed HERE, from
    `styled.text`, in a method that has no other string in scope; the style
    ranges arrive already paired with the text they index.
    """

    styled: Styled
    mentions: tuple[Mention, ...] = ()
    preview: LinkPreview | None = None

    @property
    def text(self) -> str:
        return self.styled.text

    @property
    def styles(self) -> tuple[TextStyle, ...]:
        return self.styled.styles


def _is_word(ch: str) -> bool:
    """ASCII word character: the only alphabet a handle boundary is asserted in.

    Boundary checking is done ONLY between two ASCII word characters. `@miles`
    inside `@mikeson` must not match when only `miles` is a known handle (ASCII
    on both sides: reject), while 「@阿豹同我講」 must match the handle 「阿豹」
    (CJK on both sides: accept), because Chinese is written without separators.
    `str.isalnum()` is true for 同 as well as for `s`, so a boundary rule
    expressed in `isalnum()` alone would be the strict one everywhere.
    """
    return ch.isascii() and (ch.isalnum() or ch == "_")


#: An Instagram messaging fbid: bare digits, no `+` and no hyphens, the same
#: shape as a Discord snowflake. Kept as its own name rather than reusing
#: `_SNOWFLAKE_RE` because the two are the same REGEX and not the same RULE: a
#: change to Discord's id grammar must not re-grammar Instagram's.
_INSTAGRAM_FBID_RE = re.compile(r"[0-9]{5,25}")


def _preferred_recipient(ids: frozenset[str], *, platform: str) -> str | None:
    """The id form to put on the wire, out of the ones the ROSTER confirmed.

    **Platform-aware, because "an id form the wire will accept" is a different
    predicate per platform.**

    On `platform="discord"`: a snowflake is a bare digit string. Accept it; there
    is no hierarchy to prefer between (unlike Signal's ACI-over-E164), because the
    roster gate upstream (`_recipient`) has already intersected `identity.toml`'s
    ids with the ones THIS guild's member list reported for the account.

    On `platform="instagram"`: the messaging fbid (see the branch below).

    On every other platform (Signal): ACI first. Under sealed sender the same
    account reaches Nora as a phone number on one message and as an ACI on
    another, the ACI is the stable one, and it is the form
    `nora.platform.signal.react` already sends as `targetAuthor`.

    Anything that is neither a UUID nor an E.164 number returns `None` rather
    than being sent hopefully. A Signal USERNAME is the concrete case: it is a
    valid `identity.toml` id kind, but signal-cli's RECIPIENT grammar needs it
    prefixed `u:` and a bare one would be parsed as a phone number and rejected,
    failing the whole send (see this module's docstring). The roster gate makes
    this branch nearly unreachable on its own (`listGroups` reports numbers and
    ACIs), which is why it is a `None` and not a prefix hack.
    """
    if platform == "discord":
        snowflakes = sorted(v for v in ids if _SNOWFLAKE_RE.fullmatch(v))
        return snowflakes[0] if snowflakes else None
    if platform == "instagram":
        # The same shape as Discord's: an Instagram fbid is a bare digit string,
        # so it matches NEITHER pattern below.
        #
        # Two id kinds exist for an Instagram account and only ONE is a mention:
        # `fbid` (interop_messaging_user_fbid) is what a mention carries, `igid` is
        # the public id that profile and story reads take. Both are bare digits,
        # so no length or prefix rule separates them. The ROSTER is what
        # disambiguates: it reports the messaging id, and `_recipient` has already
        # intersected away everything it did not.
        fbids = sorted(v for v in ids if _INSTAGRAM_FBID_RE.fullmatch(v))
        return fbids[0] if fbids else None
    uuids = sorted(v for v in ids if _UUID_RE.fullmatch(v))
    if uuids:
        return uuids[0]
    numbers = sorted(v for v in ids if _E164_RE.fullmatch(v))
    return numbers[0] if numbers else None


# Deliberately re-stated here rather than imported from `nora.agent.egress`:
# those patterns exist to REFUSE a string, and are tuned to fire on a substring
# anywhere in a sentence (`\b`-anchored, `+`-required). These two decide that a
# WHOLE id is of a known form before it is sent, so they are `fullmatch`-shaped.
# Sharing one pattern between "block if this appears anywhere" and "accept only
# if this is the entire value" is how one of the two silently loosens when the
# other is tuned.
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_E164_RE = re.compile(r"\+\d{8,15}")

# A Discord snowflake: an unsigned integer, decimal, no leading `+` and no
# hyphens, an id form `_UUID_RE`/`_E164_RE` do not accept. Not shared with them,
# for the same reason they do not share one with each other: this one's job is
# "is this a whole snowflake", not "does a snowflake appear somewhere".
_SNOWFLAKE_RE = re.compile(r"\d+")


def first_url(text: str) -> str | None:
    """The first http(s) URL in `text`, as a substring of `text`, or None.

    A SUBSTRING is the contract, not merely a URL: signal-cli requires the
    preview's url to appear in the message body verbatim or the apps show no card
    at all, so a normalised, unescaped or redirect-followed URL here would produce
    a message that carries a preview payload nobody ever sees.
    """
    match = _URL.search(text)
    if match is None:
        return None
    url = match.group(0).rstrip(_URL_TAIL)
    return url or None


class OutboundComposer:
    """Turns one final reply string into its `Outbound`. Constructed per turn.

    Per turn, and not per process, because the roster cache below is scoped to
    one conversation and one moment (as `nora.agent.room.Room` is per tag).
    Collaborators arrive through `__init__` only (CLAUDE.md's test-seam policy);
    there is no module-level state and nothing here is reused between turns.

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
        content_checks: bool = True,
    ) -> None:
        self._content_checks = content_checks
        self._platform = platform
        self._conversation_id = conversation_id
        self._persons = persons
        self._metrics = metrics
        self._roster_source = roster_source
        self._preview_source = preview_source
        self._handles = _handle_index(persons)
        self._roster: Roster | None = None

    async def compose(self, styled: Styled) -> Outbound:
        """`styled` plus its decoration. Never raises; never returns other text.

        **The parameter is a `Styled`, not a string, and that is the safety
        property.** `styled.text` is already final (`Deliverer.deliver` rendered
        the Markdown before it measured its guards, because a fail-closed guard has
        to see the bytes that leave), so the only string this method can reach is
        the one the platform will receive. There is no draft in scope to compute a
        mention offset against by mistake, and the style ranges that arrive with it
        were computed against this same string.

        A transformation of the outbound text added HERE would be correct for the
        mentions and WRONG for the styles, which already index `styled.text`. It
        belongs in `nora.agent.markdown.render_markdown`, where both are produced
        at once.
        """
        mentions = await self._mentions(styled.text)
        preview = await self._preview(styled.text)
        return Outbound(styled=styled, mentions=mentions, preview=preview)

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
                # Instagram-only (`Mention.handle`'s own docstring): the account's
                # OWN username, if the operator recorded one beside this exact
                # fbid. Looked up here, not in `send.py`, because this method
                # already resolved `recipient` against THIS room's live roster, so
                # `nora.platform.instagram.send` gets a value already tied to the
                # right account. `None` on every other platform and on Instagram
                # until an operator opts a person in; both leave the span exactly
                # as Nora wrote it.
                handle=(
                    self._persons.instagram_handle(recipient)
                    if self._platform == "instagram" else None
                ),
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
            recipient = _preferred_recipient(shared, platform=self._platform)
            if recipient is None:
                # In the room, but every id form the platform gave for them is
                # one signal-cli's RECIPIENT grammar would not take. Counted under
                # `unresolved`: the operator's action is the same as for an
                # unmapped person (fix identity.toml), and `rule`/`outcome` values
                # are append-only like the metric names (DEC-33).
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
        # R-18 on somebody ELSE's text. `scan_identifiers`, NOT `scan_reply`
        # (the distinction is `nora.agent.egress`'s, DEC-30): the identifier
        # floor applies to everything that leaves, including a fetched page's
        # title, because a number rendered in a card is in the room exactly as
        # much as one she typed. The model-name half does not, because that rule
        # is «she does not SAY what she runs on» and a third party's page title
        # is not her saying anything; screening it would drop the preview of any
        # page about LLMs.
        kind = scan_identifiers(
            f"{preview.title} {preview.description}", content_checks=self._content_checks
        )
        if kind:
            log.error("preview_blocked: %s in the fetched card for %s", kind, preview.url)
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            self._metrics.inc("nora_link_previews_total", outcome="blocked")
            return None
        self._metrics.inc("nora_link_previews_total", outcome="attached")
        return preview


def _handle_index(persons: Persons) -> dict[str, str | None]:
    """Every name Nora may be tagged with, longest first, `None` if ambiguous.
    Built from `name`, `first_name` and `aliases`, the SAME three fields
    `nora.agent.room.RoomView.find` matches a lookup target on. An alias never
    resolves a SENDER (a display name is spoofable, and matching one would hand
    identity to whoever can type it), but this is not a sender question: the names
    are operator-authored in `identity.toml`, the text being matched is Nora's
    OWN, and the answer is then narrowed to the room's actual members before
    anything is sent.

    A handle claimed by two people maps to `None` rather than to one of them (see
    `_match_handle`). The dict is insertion-ordered longest-first so
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
