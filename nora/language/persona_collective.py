"""The collective Persona sessions — a room's, the unit's and Nora's own
account — sharing the person session's checked writer
(design/unified-memory/45 §4: «The unit, room (Culture) and Nora accounts
follow the same shape with their own inputs»).

One implementation because settlement and validation are identical
(`PersonaPersonAdaptor`'s verbs, accept and commit); the three registered
instances keep distinct names, populations, evidence and cursors, and each
entity's own inputs:

- **a room** (`persona-room`): the room's visible messages, except hers —
  her own words can never support a claim (the echo rule);
- **the unit** (`persona-unit`): its rooms' cultures, and the Record rows
  those cite (prose is context, never a substitute for a citation's time,
  speaker and room);
- **Nora** (`persona-nora`): the unit's closed response episodes — how the
  room answered her. Her sends are read for their time and never made
  citable.

No presence is read (R-31), and no stimulus wake exists here: a turn's
`told` line or dispute names a PERSON's account, never a collective's, so
there is nothing to wake on but the evidence count and the stale rule.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    render_group,
    render_row_window,
)
from nora.domain.culture import Episode, RoomMessage
from nora.domain.persona import (
    PERSONA_HKT,
    Account,
    PersonaCitable,
    Said,
    handles_for,
    identity_text,
)
from nora.domain.prompt import age, escape
from nora.language.character import CharacterRoom, room_cultures_block
from nora.language.culture import CULTURE_TOOLS
from nora.language.persona import (
    PersonaPersonAdaptor,
    _Culture,
    _line_attrs,
    _Persona,
    _Reads,
    _Run,
    _said,
)
from nora.obs.metrics import Metrics

__all__ = [
    "PERSONA_COLLECTIVE_ADAPTOR_NAMES",
    "PERSONA_NORA_ADAPTOR_NAME",
    "PERSONA_ROOM_ADAPTOR_NAME",
    "PERSONA_UNIT_ADAPTOR_NAME",
    "CollectiveEvidence",
    "CollectiveKey",
    "PersonaCollectiveAdaptor",
]

PERSONA_ROOM_ADAPTOR_NAME = "persona-room"
PERSONA_UNIT_ADAPTOR_NAME = "persona-unit"
PERSONA_NORA_ADAPTOR_NAME = "persona-nora"
PERSONA_COLLECTIVE_ADAPTOR_NAMES = (
    PERSONA_ROOM_ADAPTOR_NAME,
    PERSONA_UNIT_ADAPTOR_NAME,
    PERSONA_NORA_ADAPTOR_NAME,
)

#: `inputs` holds six read sites (including the conditional Persona room read):
#: the account's revision, its lines, the
#: previous prose head, the rooms' cultures in one gather, cited rows in one
#: read); `commit`'s one write is the parent's, counted in its own
#: declaration.
COLLECTIVE_STORE_CALLS_PER_TICK = 6

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("about_entity", 5, "turn"),
    ("persona_lines", 10, "turn"),
    ("previous_character", 20, "turn"),
    ("persona_messages", 40, "turn"),
    ("persona_episodes", 50, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {name: (order, key) for name, order, key in _BLOCKS}


def _at(name: str) -> dict[str, Any]:
    order, key = _ROW[name]
    return {"order": order, "key": key}


@dataclass(frozen=True)
class CollectiveKey:
    """What a collective key names: its unit, its rooms (a room account's own
    room; the unit's rooms for the unit and for Nora), and what the operator
    calls it."""

    unit: str
    rooms: tuple[CharacterRoom, ...]
    label: str = ""
    description: str = ""


class _CollectiveReads(_Reads, Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`, with the room and
    unit-wide reads the collective evidence adds."""

    async def room_count(self, *, rooms: Sequence[tuple[str, str]], after: int) -> int: ...

    async def room_since(
        self, *, rooms: Sequence[tuple[str, str]], after: int, limit: int,
    ) -> tuple[list[RoomMessage], int]: ...

    async def persona_revision_count(self, *, keys: Sequence[str]) -> int: ...

    async def persona_revisions_after(
        self,
        *,
        keys: Sequence[str],
        after: int,
        limit: int,
    ) -> tuple[list[str], int]: ...

    async def revision_count(self, *, keys: Sequence[str]) -> int: ...

    async def revisions_after(
        self, *, keys: Sequence[str], after: int, limit: int,
    ) -> tuple[list[str], int]: ...

    async def episode_count(self, *, unit: str, after: int) -> int: ...


class CollectiveEvidence:
    """The dream loop's count and read for a collective key: a room's
    messages past the watermark, a unit's rooms' new culture revisions
    (counted — the cursor is how many there are), or the unit's closed
    response episodes, by her send's Record id."""

    def __init__(
        self,
        *,
        store: _CollectiveReads,
        keys: Mapping[str, CollectiveKey],
        kind: Literal["room", "unit", "nora"],
        render: bool = False,
    ) -> None:
        self._store = store
        self._keys = keys
        self._kind = kind
        self._render = render
        self._held: dict[str, int] = {}
        # Culture revision counts and Persona revision sums are different clocks.
        # Keep the legacy row for rollback; neither counter can seed the other.
        self.watermark_name = "persona-unit:persona" if kind == "unit" and render else None

    def held(self, key: str) -> int | None:
        return self._held.get(key)

    async def count(self, *, key: str, after: int) -> int:
        ck = self._keys[key]
        if self._kind == "unit":
            return max(
                0,
                await (
                    self._store.persona_revision_count(keys=[r.key for r in ck.rooms])
                    if self._render
                    else self._store.revision_count(keys=[r.key for r in ck.rooms])
                )
                - after,
            )
        if self._kind == "nora":
            return await self._store.episode_count(unit=ck.unit, after=after)
        return await self._store.room_count(
            rooms=[(r.platform, r.key) for r in ck.rooms], after=after)

    async def newest(self, *, key: str, after: int, limit: int) -> tuple[Sequence[Any], int]:
        ck = self._keys[key]
        self._held[key] = after
        if self._kind == "unit":
            if self._render:
                return await self._store.persona_revisions_after(
                    keys=[r.key for r in ck.rooms], after=after, limit=limit
                )
            return await self._store.revisions_after(
                keys=[r.key for r in ck.rooms], after=after, limit=limit
            )
        if self._kind == "nora":
            return await self._store.episodes(unit=ck.unit, after=after, limit=limit,
                                              evidence_only=True)
        return await self._store.room_since(
            rooms=[(r.platform, r.key) for r in ck.rooms], after=after, limit=limit)

    # A collective has no per-source cursor to keep across runs (45 §4 is the
    # person account's social items only), so the `_Evidence` cursor verbs are
    # no-ops here; the watermark the loop holds is `_held`.
    def historical(self, key: str) -> bool:
        return False

    def handed(self, key: str, items: Sequence[Any]) -> None:
        pass

    def forget(self, key: str) -> None:
        pass

    def gave_up(self, key: str) -> None:
        pass


class PersonaCollectiveAdaptor(PersonaPersonAdaptor):
    """The same atomic Persona session, with each collective's own evidence."""

    name = PERSONA_ROOM_ADAPTOR_NAME
    objective = PERSONA_ROOM_ADAPTOR_NAME
    store_calls_per_tick = COLLECTIVE_STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        kind: Literal["room", "unit", "nora"],
        persona: _Persona,
        culture: _Culture,
        evidence: CollectiveEvidence,
        reads: _CollectiveReads,
        keys: Mapping[str, CollectiveKey],
        name_of: Callable[[str | None], str],
        clock_ms: Callable[[], int],
        metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
        render: bool = False,
    ) -> None:
        super().__init__(
            persona=persona,
            culture=culture,
            evidence=evidence,
            reads=reads,
            keys={},
            name_of=name_of,
            clock_ms=clock_ms,
            metrics=metrics,
            clock=clock,
            render=render,
        )
        # Re-stated, not re-read: the store-calls walk
        # (tests/architecture/test_dream_store_calls_are_declared.py) reads a
        # class's collaborators off THIS class's `__init__` assignments, and
        # the parent's are invisible to it.
        self._persona = persona
        self._render = render
        self._culture = culture
        self._reads = reads
        self._collective_keys = keys
        self._population = tuple(sorted(keys))
        self._kind = kind
        self.name = self.objective = f"persona-{kind}"
        #: A room's session keeps Culture's read tools, scoped to its room;
        #: the unit's and Nora's reason over what they are handed.
        self.tools = CULTURE_TOOLS if kind == "room" else frozenset()

    def tool_room(self, key: str) -> str | None:
        return key if self._kind == "room" and key in self._collective_keys else None

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """What the entity is, its held lines, the previous prose head until
        the account first settles, and the entity's own evidence. `""` when
        nothing is new: no model call."""
        ck = self._collective_keys.get(key)
        if ck is None:
            return ""
        # The revision before the lines: a write landing between the two reads
        # makes this session's write a `conflict`, never a write on shifted handles.
        revision, settled_at = await self._persona.account_state(key=key)
        heads = await self._persona.account_heads(key=key)
        allowed = frozenset(r.key for r in ck.rooms)
        messages = [m for m in batch if isinstance(m, RoomMessage)
                    and m.room in allowed and not m.row.is_nora]
        episodes = [e for e in batch if isinstance(e, Episode) and e.room in allowed
                    and e.outcome not in ("open", "unknown") and e.is_evidence]
        if not (messages or episodes or (self._kind == "unit" and batch)):
            return ""
        # THE BACKFILL (35 §4), the person session's rule: until a session
        # first settles the account it is handed the prose head under the same
        # key (the room's culture, the unit's or Nora's Character account) and
        # the rows it cited, once.
        previous = await self._culture.head(key=key) if settled_at is None else None
        cultures = await asyncio.gather(
            *(
                (
                    self._persona.room_context(key=r.key)
                    if self._render
                    else self._culture.head(key=r.key)
                )
                for r in ck.rooms
                if self._kind == "unit"
            )
        )
        # Read the underlying rows: prose culture is context, never a
        # substitute for a citation's time, speaker and room. The episode
        # sends join ONLY to read their time; her words are never citable
        # (the echo rule).
        cited = {int(c) for h in [previous, *cultures] if h is not None
                 for c in h.citations if c.isdigit()}
        cited |= {e.send_id for e in episodes}
        found = await self._reads.rows_by_id(ids=sorted(cited))
        grounded = [m for m in found if m.room in allowed]
        old = [m for m in grounded if not m.row.is_nora]
        # The rows the PROSE HEAD cited, for its `cites` attribute — the
        # person session's rendering, so the session may cite what it cited.
        prev_ids = {int(c) for c in previous.citations if c.isdigit()} if previous else set()
        old_cited = [m for m in old if m.row.id in prev_ids]
        messages = list({m.row.id: m for m in [*old, *messages]}.values())
        # An episode whose send's time cannot be read is unavailable, never
        # guessed (R-27).
        stamps = {m.row.id: m.row.ts for m in grounded}
        episodes = [e for e in episodes if e.send_id in stamps]
        replies = [RoomMessage(room=e.room, row=r) for e in episodes
                   for r in e.replies if not r.is_nora]
        said = {m.row.id: _said(m) for m in [*messages, *replies]}
        self._runs[key] = _Run(
            account=Account(key=key, entity_kind=self._kind, unit=ck.unit),
            citable=PersonaCitable(
                messages=said,
                episodes={e.send_id: Said(stamps[e.send_id], None, e.room)
                          for e in episodes},
                listed_rooms=allowed,
            ),
            revision=revision, heads=heads, batch_start=self.evidence.held(key),
            tellers={self._name_of(s.person_id).casefold(): s.person_id
                     for s in said.values() if s.person_id is not None},
        )
        identity_names = {s.person_id for s in said.values()}
        def identity_name(pid: str) -> str:
            return self._name_of(pid) if pid in identity_names else "someone"
        now = self._clock()
        handle_of = {i: h for h, i in handles_for(heads).items()}
        labels = {r.key: r.label for r in ck.rooms}
        parts: list[ContextBlock | None] = [
            ContextBlock(
                name="about_entity",
                **_at("about_entity"),
                when_empty="self_close",
                attrs=(
                    ("kind", self._kind),
                    ("name", ck.label or self._kind),
                    ("rooms", ", ".join(r.label or "unnamed room" for r in ck.rooms)),
                ),
                body=escape(ck.description),
            ),
            None
            if not handle_of
            else ContextBlock(
                name="persona_lines",
                **_at("persona_lines"),
                body=render_elements(
                    [h for h in heads if h.id in handle_of],
                    tag="line",
                    attrs=lambda h, _i: _line_attrs(h, handle_of[h.id], now, self._name_of),
                    body=lambda h: (
                        identity_text(h, name_of=identity_name) or "legacy untyped: " + h.text
                    ),
                ),
            ),
            None
            if previous is None
            else ContextBlock(
                name="previous_character",
                **_at("previous_character"),
                body=escape(previous.body),
                attrs=(("written", age(previous.written_at, now)),)
                + ((("cites", ", ".join(str(m.row.id) for m in old_cited)),) if old_cited else ()),
            ),
            room_cultures_block(ck.rooms, list(cultures), now, cites=True) if cultures else None,
            None
            if not messages
            else ContextBlock(
                name="persona_messages",
                **_at("persona_messages"),
                body=render_row_window(
                    sorted(messages, key=lambda m: (m.row.ts, m.row.id)),
                    row_id=lambda m: m.row.id,
                    attrs=lambda m: (
                        ("from", self._name_of(m.row.person_id)),
                        ("room", labels.get(m.room) or "unnamed room"),
                        ("age", age(m.row.ts, now)),
                        ("source_at", datetime.fromtimestamp(
                            m.row.ts / 1000, PERSONA_HKT).isoformat()),
                    ),
                    body=lambda m: m.row.body or "",
                ),
            ),
            None
            if not episodes
            else ContextBlock(
                name="persona_episodes",
                **_at("persona_episodes"),
                body="\n".join(
                    render_group(
                        "episode",
                        [
                            ("n", f"response:{e.send_id}"),
                            ("room", labels.get(e.room) or "unnamed room"),
                            ("age", age(stamps[e.send_id], now)),
                            ("outcome", e.outcome),
                            ("reactions", str(e.reactions)),
                            ("edits", str(e.edits)),
                        ],
                        render_row_window(
                            [
                                (e.send_id, "you", e.text),
                                *(
                                    (r.id, self._name_of(r.person_id), r.body or "")
                                    for r in e.replies
                                    if not r.is_nora
                                ),
                            ],
                            row_id=lambda m: m[0],
                            attrs=lambda m: (("from", m[1]),),
                            body=lambda m: m[2],
                        ),
                    )
                    for e in episodes
                ),
            ),
        ]
        return assemble([p for p in parts if p is not None], sep="\n\n")
