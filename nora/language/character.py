"""Character sessions for the entities that are not a room.

**One Dreamer session per (unit, entity) key, woken by that entity's own
evidence.** A room is `nora.language.culture.CultureAdaptor`. This module holds
the others, each its own adaptor with its own key kind, evidence count, wake
batch, objective and block rows, and all of them writing through the same
verbs (`nora.language.character_session.EntitySessions`) into the same culture
store and revision chain:

- **a person** (C2b), key `character_key(unit, person_id)`: one account per
  (unit, person listed in that unit's rooms). Its evidence is the person's
  human messages across the unit's rooms since its watermark. It is handed its
  previous account, those messages (newest `batch_limit`, each with its Record
  id and room) and the current culture of each of the unit's rooms, read by
  code from the store. Memory is a tool it MAY call (`ask_memory`,
  `search_memory`), never a pre-fetch: the account must not depend on Honcho.
  Its citations are the ids of the messages it was handed. Its evidence
  also holds the person's social items not yet handed to a session
  (design/cognition-loop/22 §6): only the polled ones count toward the wake,
  and polled and back-filled alike are handed, the oldest recorded first,
  at most `SOCIAL_ITEMS_MAX` a session, as `<social_items>`, each cited
  `social:<network>:<item id>`.
- **the unit** (C3a), key `unit_culture_key(unit)`: what the unit is across
  its rooms. Its evidence is new revisions of its rooms' cultures. It is
  handed its previous account and each room's current culture with the
  Record ids that culture cites; it cites from those (a room's culture is
  linked, never cited, DEC-249, so the chain still ends in the Record). It
  holds no read tool. Rendered as `<unit_culture>` in the unit's rooms.
- **Nora** (C3b), key `nora_character_key(unit)`: what she is like in this
  unit, as its people have met her. Its evidence is Rollup `response`
  episodes of the current build that are not `open` or `unknown`: each of her
  sends paired with the replies that quoted it, its reaction and edit counts
  and its outcome. It cites episodes people responded to (`response:<id>`,
  outcome `responded` or `silent`) or the replies, and the verb enforces the ECHO
  RULE: a revision resting only on her own sends is
  refused. Descriptive only, no read tool; rendered as `<self>`.

**Code at the edges, an agent loop in the middle**: code counts the
evidence, wakes the key, hands over the previous account and what is new, and
validates every write; the model decides what to look up and what the account
now says.

**Lineage, chosen and stated.** An account is prose with no sections (DEC-184),
so it has no section to drop. `[lineage]` is off and marked do-not-enable
(AGENTS.md «A boundary is drawn by scope»): the unit is the read boundary, and
a person's account renders only in that unit's rooms that list them. What would
have put presence into it is left out instead: a person session holds no
presence verb (`where_been`, `online_now`), because its account renders in
rooms whose `location` flags differ, and R-31 is never simplified away.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    render_group,
    render_row_window,
)
from nora.domain.culture import (
    CHARACTER_KINDS,
    CHARACTER_OUTCOMES,
    NORA_MIN_EVIDENCE_EPISODES,
    Citable,
    CultureProposal,
    CultureRevision,
    Episode,
    RoomMessage,
    social_citation,
)
from nora.domain.media import media_detail, sanitize_description
from nora.domain.memory_index import social_label
from nora.domain.models import PersonRef
from nora.domain.ports import Proposal
from nora.domain.prompt import age, escape, stamp
from nora.domain.social_stream import PersonSocialItem, RecordedCounts, to_us
from nora.language.character_session import AccountNotRecordableError, EntitySessions
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "CHARACTER_NORA_ADAPTOR_NAME",
    "CHARACTER_PERSON_ADAPTOR_NAME",
    "CHARACTER_PERSON_TOOLS",
    "CHARACTER_UNIT_ADAPTOR_NAME",
    "CharacterNoraAdaptor",
    "CharacterPersonAdaptor",
    "CharacterRoom",
    "CharacterUnitAdaptor",
    "NoraEvidence",
    "NoraKey",
    "PersonEvidence",
    "PersonKey",
    "UnitEvidence",
    "UnitKey",
    "room_cultures_block",
]

#: The person adaptor's `name`: its watermark identity, schedule, budget and
#: grant key, and its objective file.
CHARACTER_PERSON_ADAPTOR_NAME = "character-person"

#: The read verbs a person session holds, each for what it reads that the
#: handed messages do not:
#:
#: - `ask_memory`, `search_memory`: what Nora has concluded about them, and
#:   what they said before this batch. A hint the account may weigh, never a
#:   citation (DEC-249) and never a pre-fetch.
#: - `about`, `lookup_person`, `group_members`: who a name or handle in their
#:   messages IS, against the room's roster.
#:
#: Deliberately absent: `where_been`, `online_now` (presence, R-31; module
#: docstring), every act verb (the read-only ceiling), and `web_search`,
#: `web_fetch`: the design never asked a person session to look a reference up,
#: so they were the least that was not needed (DEC-290).
#:
#: `memory_search` is the same reach on the graph surface (DEC-316): built
#: instead of `ask_memory` and `search_memory` while `[memory.index] read`.
CHARACTER_PERSON_TOOLS: frozenset[str] = frozenset({
    "ask_memory", "search_memory", "about", "lookup_person", "group_members",
    "memory_search",
})

#: `inputs` holds two sites (the previous account; the rooms' cultures, one
#: gather) and `commit` two (a `head` and an `append`).
PERSON_STORE_CALLS_PER_TICK = 4

#: What each session carries, in the order the model reads it.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("about_unit", 5, "turn"),
    ("about_person", 10, "turn"),
    ("previous_character", 20, "turn"),
    ("room_cultures", 30, "turn"),
    ("person_messages", 40, "turn"),
    ("social_items", 45, "turn"),
    ("episodes", 50, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


def _at(name: str) -> dict[str, Any]:
    """`order` and `key` for a `ContextBlock(name=...)` from the table."""
    order, key = _ROW[name]
    return {"order": order, "key": key}


@dataclass(frozen=True)
class CharacterRoom:
    """One room of a unit, as a Character session needs it: its culture key
    (`primary_id`), its platform and its policy label."""

    key: str
    platform: str
    label: str


@dataclass(frozen=True)
class PersonKey:
    """What a person key names: the unit, the person, the unit's rooms (where
    their evidence is counted and whose cultures they are handed), and the
    room their read tools stand in (the first of the unit's rooms that lists
    them, so a roster lookup resolves them)."""

    unit: str
    person: PersonRef
    rooms: tuple[CharacterRoom, ...]
    home: str
    #: The rooms that list them (`primary_id`s): where evidence about them
    #: counts for their Profile (design/unified-memory/35 §3).
    listed: tuple[str, ...] = ()


#: The most social items one person session is handed: the OLDEST not yet
#: handed, so a back-fill of 50 drains over successive sessions, each item once.
SOCIAL_ITEMS_MAX = 6

#: The longest caption the block carries, in characters. A caption runs to a
#: few thousand; the account wants what kind of thing they post, not the text.
SOCIAL_CAPTION_CHARS = 125

#: The longest `shows` one item carries, in characters, all its media
#: together (each reading is first clipped to `[media]
#: comprehension_render_chars`): a ten-picture carousel is one item.
SOCIAL_SHOWS_CHARS = 120

#: The most the whole rendered `<social_items>` block may be, in characters:
#: its opening tag (its description and field notes, about 1,100 with the
#: counts' four notes), and per item its caption, its
#: `shows` and its attributes (a citation, a kind, a posted time: under 350
#: together, plus under 60 of counts when the session reads them).
#: `total_tokens` counts every model call's whole input, so the block is paid
#: once per call: at `OUTPUT_TOKENS_PER_CHAR`'s dense-Cantonese 1.15 it is at
#: most 3,910 tokens a call, and at `[dreamer.budgets.culture]`'s `turns = 10`
#: at most 39,100, a third of its `total_tokens = 120000`
#: (`tests/integration/test_character_social.py` holds both). The rest of the
#: session (its messages, the room cultures, the instruction, its writes)
#: keeps the other two thirds.
# ponytail: a fixed bound; a config key once a session's token use with the
# block is read back.
SOCIAL_BLOCK_BUDGET_CHARS = 3_400


class _PersonMessages(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s person reads."""

    async def message_head(self) -> int: ...

    async def social_cursor(self, *, adaptor: str, key: str) -> tuple[int, int]: ...

    async def advance_social_cursor(
        self, *, adaptor: str, key: str, cursor: int, read_at: int,
    ) -> None: ...

    async def person_count(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> int: ...

    async def person_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]: ...


class _PersonSocial(Protocol):
    """`nora.infra.sql.social_stream.SqlSocialStreamStore`'s Character reads:
    gone, hidden and opted-out items are left out there."""

    async def polled_count(self, *, persons: Sequence[str], after_us: int) -> int: ...

    async def now(self) -> datetime: ...

    async def person_items(
        self, *, persons: Sequence[str], after_us: int, limit: int,
    ) -> list[PersonSocialItem]: ...


@dataclass
class _Read:
    """One social read in flight for a key: where it started, when (the
    database's clock, in microseconds) and how far it reaches once the
    session's items are known (`handed`)."""

    start: int
    at: int
    reach: int


class PersonEvidence:
    """The dream loop's count and read for a person key (`nora.app.runtime`
    `_KeyEvidence`): that person's messages across their unit's rooms and,
    with `social`, their social items not yet handed to a session.

    **Two cursors.** Messages go by the key's watermark, a Record id. Social
    items have no Record id, so they go by their own cursor (`social_cursor`:
    when the last item handed was recorded, and when the read that handed it
    ran). `newest` reads the oldest `SOCIAL_ITEMS_MAX` items past it; the
    session says which of them it rendered (`handed`); `settle`, which the
    dream loop calls only after a run that is not `unavailable` (the same rule
    as the watermark), moves the cursor past those, unless the session gave
    up on its batch (`gave_up`). So a failed run hands the same items again,
    and a backlog past the bound is handed over the next sessions, every
    item once.

    **The wake.** Only polled items count (§6), only those recorded after
    the last settled read began (an item polled while a session ran counts
    toward the next wake), and at most `SOCIAL_ITEMS_MAX`: a backlog
    already waiting at that read never wakes the key again, so it cannot fire
    sessions back to back.

    **A batch a session cannot settle** (DEC-280 for messages): the first
    time it is abandoned its items are kept for the next session; abandoned
    again from the same position, they are consumed, so the key advances."""

    def __init__(
        self, *, store: _PersonMessages, keys: Mapping[str, PersonKey],
        social: _PersonSocial | None = None,
        former_ids: Callable[[str], Sequence[str]] = lambda _person: (),
    ) -> None:
        """`former_ids` is `Persons.former_ids_for`: an item recorded under a
        person's retired id is theirs too."""
        self._store = store
        self._keys = keys
        self._social = social
        self._former_ids = former_ids
        self._reads: dict[str, _Read] = {}
        # ponytail: in memory, so a restart forgets one abandonment and a
        # poison batch is abandoned once more before it is consumed; the
        # message side's `_rejections` makes the same trade.
        #: `key -> the position its batch was last abandoned from`.
        self._abandoned: dict[str, int] = {}
        self._held: dict[str, int] = {}
        #: `key -> (after, head)`: a count over `(after, head]` found nothing
        #: of theirs, so the next one starts at `head`.
        self._clear: dict[str, tuple[int, int]] = {}

    def held(self, key: str) -> int | None:
        """The watermark the last `newest` read for `key` started after: the
        name of the batch the session in flight was handed (`_begin`)."""
        return self._held.get(key)

    def _of(self, key: str) -> tuple[list[tuple[str, str]], str]:
        pk = self._keys[key]
        return [(r.platform, r.key) for r in pk.rooms], pk.person.person_id

    async def count(self, *, key: str, after: int) -> int:
        """Their messages after `after`, counted ONCE over the unit's whole
        history and thereafter only over what is newer than the last count that
        found none. A listed person who never posts has a watermark that never
        moves, so without this every tick would rescan every message of the
        unit's rooms for them. The read is `newest`, which does start at the
        watermark, so a message this count's floor skipped (an id that committed
        out of order, below the head read) still reaches the batch the next
        wake reads. In memory: a restart is one more full count. Then their
        social items, as the class docstring says."""
        rooms, person_id = self._of(key)
        scanned = self._clear.get(key)
        floor = after
        if scanned is not None and scanned[0] <= after < scanned[1]:
            floor = scanned[1]
        # The head BEFORE the count: a message that lands during it is above
        # the head, so the next count sees it.
        head = await self._store.message_head()
        n = await self._store.person_count(rooms=rooms, person_id=person_id, after=floor)
        if n == 0 and head > floor:
            self._clear[key] = (min(after, floor), head)
        if self._social is None:
            return n
        cursor, read_at = await self._store.social_cursor(
            adaptor=CHARACTER_PERSON_ADAPTOR_NAME, key=key)
        polled = await self._social.polled_count(
            persons=self._ids(person_id), after_us=max(cursor, read_at))
        return n + min(polled, SOCIAL_ITEMS_MAX)

    def _ids(self, person_id: str) -> tuple[str, ...]:
        """Every id their social items may be recorded under."""
        return (person_id, *self._former_ids(person_id))

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[RoomMessage | PersonSocialItem], int]:
        """Their newest `limit` messages past `after` and the cursor past all
        of them, then the oldest `SOCIAL_ITEMS_MAX` social items not yet
        handed. The returned cursor is the messages' alone; the items' is
        moved by `settle`."""
        rooms, person_id = self._of(key)
        self._held[key] = after
        self._reads.pop(key, None)
        messages, cursor = await self._store.person_since(
            rooms=rooms, person_id=person_id, after=after, limit=limit,
        )
        if self._social is None:
            return messages, cursor
        start, _read_at = await self._store.social_cursor(
            adaptor=CHARACTER_PERSON_ADAPTOR_NAME, key=key)
        # The database's clock BEFORE the read: an item recorded after it is
        # not in this batch, so it counts toward the next wake.
        at = to_us(await self._social.now())
        items = await self._social.person_items(
            persons=self._ids(person_id), limit=SOCIAL_ITEMS_MAX, after_us=start,
        )
        self._reads[key] = _Read(start=start, at=at, reach=start)
        return [*messages, *items], cursor

    def handed(self, key: str, items: Sequence[PersonSocialItem]) -> None:
        """The items the session for `key` was actually shown: the cursor
        may move past these and no further."""
        read = self._reads.get(key)
        if read is not None and items:
            read.reach = max(read.start, *(i.recorded_us for i in items))

    def gave_up(self, key: str) -> None:
        """The session for `key` was abandoned without settling its account.
        The first time from a position its items are kept for the next
        session; the second time from the same position they are consumed."""
        read = self._reads.get(key)
        if read is None:
            return
        if self._abandoned.get(key) == read.start:
            del self._abandoned[key]
            return
        self._abandoned[key] = read.start
        read.reach = read.start

    def forget(self, key: str) -> None:
        """The session for `key` raised: the run is `unavailable`, nothing
        settles, and the same items are read again."""
        self._reads.pop(key, None)

    async def settle(self, *, key: str) -> None:
        """The run that read `newest` for `key` was not `unavailable`: the
        cursor moves past the items its session used, and the wake's floor to
        when its read began."""
        read = self._reads.pop(key, None)
        if read is None:
            return
        if read.reach > read.start:
            self._abandoned.pop(key, None)
        await self._store.advance_social_cursor(
            adaptor=CHARACTER_PERSON_ADAPTOR_NAME, key=key, cursor=read.reach,
            read_at=read.at,
        )


class _CultureStore(Protocol):
    async def head(self, *, key: str) -> CultureRevision | None: ...

    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = ..., model: str | None = ...,
        citations: tuple[str, ...] = ...,
    ) -> CultureRevision: ...


def room_cultures_block(
    rooms: Sequence[CharacterRoom], heads: Sequence[CultureRevision | None],
    now: datetime, *, cites: bool = False,
) -> ContextBlock | None:
    """`<room_cultures>`: each room of the unit that has an account, labelled,
    with its age, and with `cites` the Record ids it rests on (a unit session
    cites through them). `None` when no room has one yet.

    **An account with no citations is skipped.** One written before the verb
    (migration `0098_culture_citations`) came from the envelope fold, which
    also held the presence reads, and what it says was never checked against
    a Record row. This block reaches accounts that render in
    every room of the unit, whose `location` flags differ (R-31); a room's own
    account only ever renders in its room. So a pre-verb account is handed to
    nobody here, and a room is read again once its own fold has revised it."""
    rows = [(room, head) for room, head in zip(rooms, heads, strict=True)
            if head is not None and head.body.strip() and head.citations]
    if not rows:
        return None
    return ContextBlock(name="room_cultures", **_at("room_cultures"), body=render_elements(
        rows, tag="room_culture",
        attrs=lambda pair, _i: (
            ("room", pair[0].label or "unnamed room"),
            ("written", age(pair[1].written_at, now)),
            ("cites", (", ".join(pair[1].citations) or None) if cites else None),
        ),
        body=lambda pair: pair[1].body,
    ))


Owners = Callable[[str], frozenset[str]]


def _shows(
    item: PersonSocialItem, *, screen: Callable[[str], str | None], render_chars: int,
    metrics: Metrics | None, person: str, owners: Owners,
) -> str | None:
    """What perception made of an item's media, each reading clipped to
    `render_chars`, other people's handles redacted (`_redact`: a reading
    can transcribe a mention sticker) and all of them together clipped to
    `SOCIAL_SHOWS_CHARS`, or None when nothing read them. A model wrote it
    about bytes a member posted, so it is screened at every render (R-51, as
    `nora.domain.media_join` does), whole and before the clip, so an
    identifier the clip would cut is still seen: one carrying an identifier
    is withheld and counted, and the item renders without it.

    A location sticker's text is not told apart from the rest of a reading:
    the describer writes it as prose, with nothing that marks it, so a place
    named on a sticker can reach the block. The account still holds no
    presence read; this is the ceiling, stated."""
    parts = [
        sanitize_description(
            media_detail(label="", title=r.title, description=r.description,
                         content=r.content),
            max_chars=render_chars,
        )
        for r in item.readings
    ]
    shows = _redact("; ".join(p for p in parts if p), person=person, owners=owners)
    if not shows:
        return None
    kind = screen(shows)
    if kind is not None:
        log.warning("character: a social item's description carried a %s identifier; "
                    "withheld", kind)
        if metrics is not None:
            metrics.inc("nora_egress_blocks_total", kind=kind)
        return None
    return _clip(shows, SOCIAL_SHOWS_CHARS)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


#: Code points that render as nothing or as blank space and could hide or
#: pad a handle: Unicode's Default_Ignorable_Code_Point set (soft hyphen,
#: combining grapheme joiner, Hangul fillers, zero-width and direction
#: marks, variation selectors, tags), plus the blank braille pattern. Every
#: Cf character counts as well (`_invisible`).
_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)

#: Of those, the ones a script needs: variation selectors (an ideograph's
#: variant, an emoji's presentation), kept after a character that is not
#: ASCII and removed after an ASCII one, where they could only split a word
#: or a handle.
_SELECTORS = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))

#: And a subdivision flag (Scotland's, say): the black flag, up to six tag
#: letters or digits, the cancel tag. Tag characters anywhere else go: they
#: spell ASCII nobody sees, a way to write to the model unseen.
_FLAG = re.compile(
    "\U0001f3f4[\U000e0030-\U000e0039\U000e0061-\U000e007a]{1,6}\U000e007f"
)


def _in(c: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    n = ord(c)
    return any(lo <= n <= hi for lo, hi in ranges)


def _invisible(c: str) -> bool:
    return unicodedata.category(c) == "Cf" or _in(c, _IGNORABLE)


def _visible(text: str) -> str:
    """`text` without its invisible code points, keeping a subdivision flag
    whole (`_FLAG`) and a variation selector that follows a character that
    is not ASCII (`_SELECTORS`)."""
    out: list[str] = []
    at = 0
    for flag in [*_FLAG.finditer(text), None]:
        for c in text[at:flag.start() if flag else len(text)]:
            if _invisible(c) and not (
                _in(c, _SELECTORS) and out and not out[-1][-1].isascii()
            ):
                continue
            out.append(c)
        if flag:
            out.append(flag.group(0))
            at = flag.end()
    return "".join(out)


#: An `@`, any whitespace after it, and every non-whitespace character after
#: that: redacted unless the run starts with the author's own username
#: (`_redact`). Nothing before or inside the run (a CJK character, `-`, a
#: combining mark, a filler, a space) lets a handle through, an email's
#: domain included: fail closed.
_HANDLE = re.compile(r"@\s*(\S+)")

#: A username's characters, at the start of an `@` run.
_NAME = re.compile(r"[A-Za-z0-9_.]+")

#: A dot, in any of the forms NFKC leaves apart: ASCII, ideographic,
#: full-width, half-width.
_DOT = "[.\u3002\uff0e\uff61]"

#: Where an Instagram link starts: any scheme, `www.`, and the profile hosts
#: (`instagram.com`, `instagr.am`, `ig.me`), with any of `_DOT`'s dots.
_LINK_START = (
    rf"(?:https?://)?(?:www{_DOT})?"
    rf"(?:instagram{_DOT}com|instagr{_DOT}am|ig{_DOT}me)/"
)

#: A link: its first path segment, and the rest of it (path, query,
#: fragment) up to whitespace or the next link.
_PROFILE = re.compile(
    rf"{_LINK_START}([^\s/?#]*)(?:(?!{_LINK_START})\S)*", re.IGNORECASE,
)


def _redact(text: str, *, person: str, owners: Owners) -> str:
    """`text` NFKC-normalized and without its invisible code points
    (`_visible`), then every Instagram link and every `@` run that is not the
    author's own written `instagram.com/…` and `@…`. The author's own link
    keeps only `instagram.com/<their handle>`, never what follows it. An `@`
    run keeps only when it starts with the author's own username (invisible
    code points removed from it, trailing dots aside), then whatever follows
    it, punctuation or CJK text, is redacted the same way. A handle names
    somebody who never chose to be in this account, and it is an identifier
    on a network; whose handle it is comes from `identity.toml` (`owners`),
    so one nobody recorded is redacted too."""
    normal = _visible(unicodedata.normalize("NFKC", text.strip()))

    def own(handle: str) -> bool:
        return bool(handle) and person in owners(handle)

    def handle(m: re.Match[str]) -> str:
        run = "".join(c for c in m.group(1) if not _invisible(c))
        name = _NAME.match(run)
        if name is not None:
            own_name = name.group(0).rstrip(".")
            if own(own_name):
                return "@" + own_name + _HANDLE.sub(handle, run[len(own_name):])
        return "@…"

    normal = _PROFILE.sub(
        lambda m: f"instagram.com/{m.group(1)}" if own(m.group(1)) else "instagram.com/…",
        normal,
    )
    return _HANDLE.sub(handle, normal)


def _caption(text: str | None, *, person: str, owners: Owners) -> str:
    """The caption, redacted (`_redact`), then clipped to
    `SOCIAL_CAPTION_CHARS`."""
    return _clip(_redact(text or "", person=person, owners=owners), SOCIAL_CAPTION_CHARS)


@dataclass(frozen=True)
class _Shown:
    """One item as the block renders it: redacted, screened and clipped once.

    `counts` is the item's latest `social_item_count` row, when the session
    reads counts (the Profile adaptor's input, 45 §4); None renders no count
    attributes, so the Character block is unchanged."""

    item: PersonSocialItem
    shows: str | None
    caption: str
    counts: RecordedCounts | None = None


def _social_block(rows: Sequence[_Shown], now: datetime) -> ContextBlock:
    """`<social_items>`: one `item` per social item, newest posted first,
    each with the citation it is cited by, what it is, when it was posted (a
    moment and an age) and what its media shows; its caption, other people's
    handles redacted, is the content. Never its location: a person session
    holds no presence, and its account renders in rooms whose `location`
    flags differ (R-31); never its `alt_text`, nor its tagged or mentioned
    handles, which the read does not return. A count is absent when the read
    did not carry it — unknown, never 0 (`ItemCounts`'s rule) — and the
    counts' own `counted` age says when they were read."""
    ordered = sorted(rows, key=lambda r: r.item.taken_at, reverse=True)

    def attrs(r: _Shown, _i: int) -> tuple[tuple[str, str | None], ...]:
        counts = r.counts
        return (
            ("cite", social_citation(r.item.network, r.item.item_id)),
            ("kind", social_label(r.item.network, r.item.kind, collab=r.item.collab)),
            ("posted", stamp(int(r.item.taken_at.timestamp() * 1000), now)),
            ("shows", r.shows),
            ("likes", None if counts is None or counts.likes is None
             else str(counts.likes)),
            ("comments", None if counts is None or counts.comments is None
             else str(counts.comments)),
            ("reposts", None if counts is None or counts.reposts is None
             else str(counts.reposts)),
            ("counted", None if counts is None
             else age(int(counts.read_at.timestamp() * 1000), now)),
        )

    return ContextBlock(name="social_items", **_at("social_items"), body=render_elements(
        ordered, tag="item", attrs=attrs, body=lambda r: r.caption,
    ))


def _within_budget(
    rows: list[_Shown], now: datetime,
) -> tuple[list[_Shown], ContextBlock | None]:
    """The rows whose rendered block is at most `SOCIAL_BLOCK_BUDGET_CHARS`,
    measured on the final text, escaping included (a caption of `&` grows
    fivefold), and the block. Whole items go from the most recently recorded
    end, every item of a microsecond together, so the ones kept are a prefix
    of the read and the cursor moves past nothing left out. Only when the
    oldest microsecond's items alone overflow do single items go, and the
    cursor passes those unread: a session that could read nothing would hold
    the cursor there for good."""
    kept = sorted(rows, key=lambda r: r.item.recorded_us)
    while kept:
        block = _social_block(kept, now)
        if len(block.render()) <= SOCIAL_BLOCK_BUDGET_CHARS:
            return kept, block
        last = kept[-1].item.recorded_us
        older = [r for r in kept if r.item.recorded_us < last]
        kept = older if older else kept[:-1]
    return [], None


class CharacterPersonAdaptor(EntitySessions):
    """One person's account in one unit, per wake of their own evidence."""

    name = CHARACTER_PERSON_ADAPTOR_NAME
    #: Not a room and not a unit: the loop asks `keys()` for its population
    #: and `evidence` for its count (`nora.app.runtime.dream_loop`).
    key_kind = "entity"
    uses_watermark = True
    objective = CHARACTER_PERSON_ADAPTOR_NAME
    #: Unread: answers through `verbs`, never an envelope.
    envelope_key = "account"
    tools: frozenset[str] = CHARACTER_PERSON_TOOLS
    store_calls_per_tick = PERSON_STORE_CALLS_PER_TICK

    def __init__(
        self, *, culture: _CultureStore, evidence: PersonEvidence,
        keys: Mapping[str, PersonKey], screen: Callable[[str], str | None],
        render_chars: int, metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
        handle_owners: Callable[[str], frozenset[str]] = lambda _handle: frozenset(),
    ) -> None:
        """`screen` is `nora.agent.egress.scan_identifiers`, injected because
        this layer may not import it; `render_chars` bounds each medium's
        reading (`[media] comprehension_render_chars`); `handle_owners` says
        whose Instagram username a handle is
        (`Persons.instagram_username_owners`; by default nobody's, so every
        handle in a caption is redacted)."""
        self._culture = culture
        #: Read by the dream loop, never awaited here.
        self.evidence = evidence
        self._keys = keys
        self._screen = screen
        self._render_chars = render_chars
        self._handle_owners = handle_owners
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sessions_init()

    def keys(self) -> Sequence[str]:
        return tuple(sorted(self._keys))

    def tool_room(self, key: str) -> str | None:
        pk = self._keys.get(key)
        return None if pk is None else pk.home

    def _count(self, outcome: str) -> None:
        _inc_session(self._metrics, "person", outcome)

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """Who this is, the account written last time, the current culture of
        each of the unit's rooms, their messages (newest last, each with its
        Record id and room) and their social items (newest first, each with
        its citation). `""` with neither: the runtime then does not call a
        model."""
        pk = self._keys.get(key)
        messages = [m for m in batch if isinstance(m, RoomMessage)]
        social = [m for m in batch if isinstance(m, PersonSocialItem)]
        if pk is None or not (messages or social):
            return ""
        now = self._clock()
        labels = {r.key: r.label for r in pk.rooms}
        person = pk.person.person_id
        shown, social_block = _within_budget([
            _Shown(
                item=it,
                shows=_shows(it, screen=self._screen, render_chars=self._render_chars,
                             metrics=self._metrics, person=person,
                             owners=self._handle_owners),
                caption=_caption(it.caption, person=person, owners=self._handle_owners),
            )
            for it in social
        ], now)
        # Only what the block carries is handed, cited, and passed by the cursor.
        self.evidence.handed(key, [r.item for r in shown])
        if not (messages or shown):
            return ""
        self._begin(
            key, citable=Citable(
                messages=frozenset(m.row.id for m in messages),
                social=frozenset(f"{r.item.network}:{r.item.item_id}" for r in shown),
            ),
            entities={pk.person.name: None}, batch_start=self.evidence.held(key),
        )
        previous = await self._culture.head(key=key)
        heads = await asyncio.gather(*(self._culture.head(key=r.key) for r in pk.rooms))
        parts: list[ContextBlock | None] = [
            ContextBlock(
                name="about_person", **_at("about_person"), body="", when_empty="self_close",
                attrs=(("name", pk.person.name),) + (
                    (("aliases", ", ".join(pk.person.aliases)),)
                    if pk.person.aliases else ()
                ),
            ),
            None if previous is None else ContextBlock(
                name="previous_character", **_at("previous_character"),
                body=escape(previous.body),
                attrs=(("written", age(previous.written_at, now)),),
            ),
            room_cultures_block(pk.rooms, heads, now),
            ContextBlock(name="person_messages", **_at("person_messages"), body=render_row_window(
                messages,
                row_id=lambda m: m.row.id,
                attrs=lambda m: (("room", labels.get(m.room) or "unnamed room"),),
                body=lambda m: m.row.body or "",
            )),
            social_block,
        ]
        return assemble([p for p in parts if p is not None], sep="\n\n")

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """`EntitySessions.accept`, and the social items' cursor told whether
        they were used. A session that raised is retried with the same items
        (`forget`). One abandoned after `MAX_CONSECUTIVE_REJECTIONS` keeps them
        for the next session once, and consumes them if that one is
        abandoned too (`PersonEvidence.gave_up`). A session that kept its
        account unchanged used them: it read them and judged they add
        nothing, and handing them again would hold every newer item behind
        them."""
        try:
            out = await super().accept(key=key, items=items)
        except AccountNotRecordableError:
            self.evidence.forget(key)
            raise
        if not self.settled(key):
            self.evidence.gave_up(key)
        return out

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Append the staged account under the person's unit key, unless it is
        byte-identical to the head (`CultureAdaptor.commit`'s rule)."""
        written = 0
        for proposal in proposals:
            payload = proposal.payload
            if not isinstance(payload, CultureProposal):
                raise TypeError("CharacterPersonAdaptor.commit takes its own account only")
            previous = await self._culture.head(key=key)
            if previous is not None and previous.body == payload.body:
                self._count("unchanged")
                continue
            await self._culture.append(
                key=key, body=payload.body, written_at=now_ms,
                established_by=proposal.established_by,
                instruction_sha256=proposal.instruction_sha256,
                model=proposal.model, citations=payload.citations,
            )
            self._count("written")
            written += 1
        return written


CHARACTER_UNIT_ADAPTOR_NAME = "character-unit"

#: `inputs` holds two sites (the previous account; the rooms' cultures) and
#: `commit` two.
UNIT_STORE_CALLS_PER_TICK = 4


@dataclass(frozen=True)
class UnitKey:
    """What a unit key names: the unit and its rooms, in policy order."""

    unit: str
    rooms: tuple[CharacterRoom, ...]


class _RoomRevisions(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s unit reads."""

    async def revision_count(self, *, keys: Sequence[str]) -> int: ...

    async def revisions_after(
        self, *, keys: Sequence[str], after: int, limit: int,
    ) -> tuple[list[str], int]: ...


class UnitEvidence:
    """The dream loop's count and read for a unit key: new revisions of its
    rooms' cultures, counted (the cursor is how many there are)."""

    def __init__(self, *, store: _RoomRevisions, keys: Mapping[str, UnitKey]) -> None:
        self._store = store
        self._keys = keys

    async def count(self, *, key: str, after: int) -> int:
        rooms = [r.key for r in self._keys[key].rooms]
        return max(await self._store.revision_count(keys=rooms) - after, 0)

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[str], int]:
        rooms = [r.key for r in self._keys[key].rooms]
        return await self._store.revisions_after(keys=rooms, after=after, limit=limit)


class CharacterUnitAdaptor(EntitySessions):
    """The unit's own account, per wake of new room cultures (C3a)."""

    name = CHARACTER_UNIT_ADAPTOR_NAME
    key_kind = "entity"
    uses_watermark = True
    objective = CHARACTER_UNIT_ADAPTOR_NAME
    envelope_key = "account"
    #: None: everything it reasons over is handed to it (its rooms' cultures).
    tools: frozenset[str] = frozenset()
    store_calls_per_tick = UNIT_STORE_CALLS_PER_TICK

    def __init__(
        self, *, culture: _CultureStore, evidence: UnitEvidence,
        keys: Mapping[str, UnitKey], metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._culture = culture
        #: Read by the dream loop, never awaited here.
        self.evidence = evidence
        self._keys = keys
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sessions_init()

    def keys(self) -> Sequence[str]:
        return tuple(sorted(self._keys))

    def _count(self, outcome: str) -> None:
        _inc_session(self._metrics, "unit", outcome)

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """The unit's name and rooms, its previous account, and each room's
        current culture with what it cites. `""` with no new revision, or when
        no room culture cites anything (written before the verb): an account
        with nothing it could cite is not worth a model call."""
        uk = self._keys.get(key)
        if uk is None or not batch:
            return ""
        now = self._clock()
        previous = await self._culture.head(key=key)
        heads = await asyncio.gather(*(self._culture.head(key=r.key) for r in uk.rooms))
        citable = frozenset(
            int(c) for h in heads if h is not None for c in h.citations if c.isdigit()
        )
        if not citable:
            return ""
        self._begin(
            key, citable=Citable(messages=citable), entities={"unit": None},
            # No item names a unit's batch (its evidence is a count), so a
            # rejection run is any three unsettled sessions in a row.
            batch_start=None,
        )
        parts: list[ContextBlock | None] = [
            ContextBlock(
                name="about_unit", **_at("about_unit"), body="", when_empty="self_close",
                attrs=(("rooms", ", ".join(r.label or "unnamed room" for r in uk.rooms)),),
            ),
            None if previous is None else ContextBlock(
                name="previous_character", **_at("previous_character"),
                body=escape(previous.body),
                attrs=(("written", age(previous.written_at, now)),),
            ),
            room_cultures_block(uk.rooms, heads, now, cites=True),
        ]
        return assemble([p for p in parts if p is not None], sep="\n\n")

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """`CharacterPersonAdaptor.commit`'s rule, under the unit key."""
        written = 0
        for proposal in proposals:
            payload = proposal.payload
            if not isinstance(payload, CultureProposal):
                raise TypeError("CharacterUnitAdaptor.commit takes its own account only")
            previous = await self._culture.head(key=key)
            if previous is not None and previous.body == payload.body:
                self._count("unchanged")
                continue
            await self._culture.append(
                key=key, body=payload.body, written_at=now_ms,
                established_by=proposal.established_by,
                instruction_sha256=proposal.instruction_sha256,
                model=proposal.model, citations=payload.citations,
            )
            self._count("written")
            written += 1
        return written


CHARACTER_NORA_ADAPTOR_NAME = "character-nora"

#: `inputs` holds one site (the previous account) and `commit` two.
NORA_STORE_CALLS_PER_TICK = 3


@dataclass(frozen=True)
class NoraKey:
    """What a Nora key names: the unit, and its rooms' labels by `primary_id`."""

    unit: str
    rooms: tuple[CharacterRoom, ...]


class _Episodes(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s episode reads."""

    async def episode_count(self, *, unit: str, after: int) -> int: ...

    async def episodes(
        self, *, unit: str, after: int, limit: int,
    ) -> tuple[list[Episode], int]: ...


class NoraEvidence:
    """The dream loop's count and read for a Nora key: settled response
    episodes (not `open`, not `unknown`), by her send's Record id."""

    def __init__(self, *, store: _Episodes, keys: Mapping[str, NoraKey]) -> None:
        self._store = store
        self._keys = keys
        self._held: dict[str, int] = {}

    def held(self, key: str) -> int | None:
        """`PersonEvidence.held`, for a Nora key."""
        return self._held.get(key)

    async def count(self, *, key: str, after: int) -> int:
        return await self._store.episode_count(unit=self._keys[key].unit, after=after)

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[Episode], int]:
        self._held[key] = after
        return await self._store.episodes(unit=self._keys[key].unit, after=after, limit=limit)


class CharacterNoraAdaptor(EntitySessions):
    """Her own account in one unit, from how its people responded to her
    (C3b). Writes only; `<self>` renders it."""

    name = CHARACTER_NORA_ADAPTOR_NAME
    key_kind = "entity"
    uses_watermark = True
    objective = CHARACTER_NORA_ADAPTOR_NAME
    envelope_key = "account"
    #: None: the episodes are handed to it whole.
    tools: frozenset[str] = frozenset()
    store_calls_per_tick = NORA_STORE_CALLS_PER_TICK

    def __init__(
        self, *, culture: _CultureStore, evidence: NoraEvidence,
        keys: Mapping[str, NoraKey], name_of: Callable[[str | None], str],
        metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._culture = culture
        #: Read by the dream loop, never awaited here.
        self.evidence = evidence
        self._keys = keys
        #: A reply's author by `person_id` (the roster's name, never a
        #: platform id or a self-set profile name).
        self._name_of = name_of
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sessions_init()

    def keys(self) -> Sequence[str]:
        return tuple(sorted(self._keys))

    def _count(self, outcome: str) -> None:
        _inc_session(self._metrics, "nora", outcome)

    def _episode(self, e: Episode, labels: Mapping[str, str]) -> str:
        """One `<episode>`: her send and what quoted it, each an `m` with its
        Record id, inside one element carrying the episode's handle, room,
        outcome and counts."""
        rows = render_row_window(
            [(e.send_id, "you", e.text), *(
                (r.id, self._name_of(r.person_id), r.body or "") for r in e.replies
            )],
            row_id=lambda m: m[0],
            turn=lambda m: e.turn_handle if m[0] == e.send_id else None,
            attrs=lambda m: (("from", m[1]),),
            body=lambda m: m[2],
        )
        return render_group("episode", [
            ("n", f"response:{e.send_id}"),
            ("room", labels.get(e.room) or "unnamed room"),
            ("outcome", e.outcome),
            ("reactions", str(e.reactions)),
            ("edits", str(e.edits)),
        ], rows)

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """Her previous account here and the settled episodes since. `""`
        with none: no model call."""
        nk = self._keys.get(key)
        # `open` is not yet an outcome and `unknown` is an outage, not
        # silence (R-27): neither is shown, so neither can be cited. The SQL
        # read already leaves them out; this is the boundary that holds
        # whatever a store hands over.
        episodes = [e for e in batch if isinstance(e, Episode)
                    and e.outcome not in ("open", "unknown")]
        if nk is None or not episodes:
            return ""
        now = self._clock()
        labels = {r.key: r.label for r in nk.rooms}
        sends = frozenset(e.send_id for e in episodes)
        evidence = frozenset(e.send_id for e in episodes if e.is_evidence)
        self._begin(
            key, citable=Citable(
                messages=sends | frozenset(r.id for e in episodes for r in e.replies),
                nora=sends,
                # Only an episode people DID something in may be cited: her own
                # send, relabelled as an episode, would otherwise meet the echo
                # rule with nothing anyone said.
                episodes=evidence,
                echo_rule=True,
                # A floor, never above what this session was shown: a stale
                # wake on one episode could not meet a bar of three.
                min_episodes=min(NORA_MIN_EVIDENCE_EPISODES, len(evidence)),
            ),
            entities={"nora": None}, batch_start=self.evidence.held(key),
        )
        previous = await self._culture.head(key=key)
        parts: list[ContextBlock | None] = [
            None if previous is None else ContextBlock(
                name="previous_character", **_at("previous_character"),
                body=escape(previous.body),
                attrs=(("written", age(previous.written_at, now)),),
            ),
            ContextBlock(
                name="episodes", **_at("episodes"),
                body="\n".join(self._episode(e, labels) for e in episodes),
            ),
        ]
        return assemble([p for p in parts if p is not None], sep="\n\n")

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """`CharacterPersonAdaptor.commit`'s rule, under her unit key."""
        written = 0
        for proposal in proposals:
            payload = proposal.payload
            if not isinstance(payload, CultureProposal):
                raise TypeError("CharacterNoraAdaptor.commit takes her own account only")
            previous = await self._culture.head(key=key)
            if previous is not None and previous.body == payload.body:
                self._count("unchanged")
                continue
            await self._culture.append(
                key=key, body=payload.body, written_at=now_ms,
                established_by=proposal.established_by,
                instruction_sha256=proposal.instruction_sha256,
                model=proposal.model, citations=payload.citations,
            )
            self._count("written")
            written += 1
        return written


def _inc_session(metrics: Metrics | None, kind: str, outcome: str) -> None:
    """`nora_character_session_total{kind, outcome}`, refusing a label outside
    its vocabulary (a typo is a new series, not an error)."""
    if kind not in CHARACTER_KINDS or outcome not in CHARACTER_OUTCOMES:
        raise ValueError(f"nora_character_session_total{{{kind}, {outcome}}} is "
                         f"outside its vocabulary")
    if metrics is not None:
        metrics.inc("nora_character_session_total", kind=kind, outcome=outcome)
