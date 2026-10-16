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
  Its citations are the ids of the messages it was handed.
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
)
from nora.domain.models import PersonRef
from nora.domain.ports import Proposal
from nora.domain.prompt import age, escape
from nora.language.character_session import EntitySessions
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
#: instead of `ask_memory` and `search_memory` while `[memory_index] enabled`.
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


class _PersonMessages(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s person reads."""

    async def message_head(self) -> int: ...

    async def person_count(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> int: ...

    async def person_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]: ...


class PersonEvidence:
    """The dream loop's count and read for a person key (`nora.app.runtime`
    `_KeyEvidence`): that person's messages across their unit's rooms."""

    def __init__(self, *, store: _PersonMessages, keys: Mapping[str, PersonKey]) -> None:
        self._store = store
        self._keys = keys
        self._population = tuple(sorted(keys))
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
        wake reads. In memory: a restart is one more full count."""
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
        return n

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[RoomMessage], int]:
        rooms, person_id = self._of(key)
        self._held[key] = after
        return await self._store.person_since(
            rooms=rooms, person_id=person_id, after=after, limit=limit,
        )


class _CultureStore(Protocol):
    async def head(self, *, key: str) -> CultureRevision | None: ...

    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = ..., model: str | None = ...,
        citations: tuple[str, ...] = ...,
    ) -> CultureRevision: ...


def _room_cultures(
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
        keys: Mapping[str, PersonKey], metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._culture = culture
        #: Read by the dream loop, never awaited here.
        self.evidence = evidence
        self._keys = keys
        self._population = tuple(sorted(keys))
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sessions_init()

    def keys(self) -> Sequence[str]:
        return self._population

    def tool_room(self, key: str) -> str | None:
        pk = self._keys.get(key)
        return None if pk is None else pk.home

    def _count(self, outcome: str) -> None:
        _inc_session(self._metrics, "person", outcome)

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """Who this is, the account written last time, the current culture of
        each of the unit's rooms, and their messages (newest last, each with
        its Record id and room). `""` with no messages: the runtime then does
        not call a model."""
        pk = self._keys.get(key)
        messages = [m for m in batch if isinstance(m, RoomMessage)]
        if pk is None or not messages:
            return ""
        now = self._clock()
        labels = {r.key: r.label for r in pk.rooms}
        self._begin(
            key, citable=Citable(messages=frozenset(m.row.id for m in messages)),
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
            _room_cultures(pk.rooms, heads, now),
            ContextBlock(name="person_messages", **_at("person_messages"), body=render_row_window(
                messages,
                row_id=lambda m: m.row.id,
                attrs=lambda m: (("room", labels.get(m.room) or "unnamed room"),),
                body=lambda m: m.row.body or "",
            )),
        ]
        return assemble([p for p in parts if p is not None], sep="\n\n")

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
        self._population = tuple(sorted(keys))
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sessions_init()

    def keys(self) -> Sequence[str]:
        return self._population

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
            _room_cultures(uk.rooms, heads, now, cites=True),
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
        self._population = tuple(sorted(keys))
        #: A reply's author by `person_id` (the roster's name, never a
        #: platform id or a self-set profile name).
        self._name_of = name_of
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sessions_init()

    def keys(self) -> Sequence[str]:
        return self._population

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
