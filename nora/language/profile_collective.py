"""The collective Profile sessions — a room's, the unit's and Nora's own
account — sharing the person session's checked writer
(design/unified-memory/45 §4: «The unit, room (Culture) and Nora accounts
follow the same shape with their own inputs»).

One implementation because settlement and validation are identical
(`ProfilePersonAdaptor`'s verbs, accept and commit); the three registered
instances keep distinct names, populations, evidence and cursors, and each
entity's own inputs:

- **a room** (`profile-room`): the room's visible messages, except hers —
  her own words can never support a claim (the echo rule);
- **the unit** (`profile-unit`): its rooms' cultures, and the Record rows
  those cite (prose is context, never a substitute for a citation's time,
  speaker and room);
- **Nora** (`profile-nora`): the unit's closed response episodes — how the
  room answered her. Her sends are read for their time and never made
  citable.

Named presence and social sources share the person evidence shape. No stimulus wake exists here:
a turn's
`told` line or dispute names a PERSON's account, never a collective's, so
there is nothing to wake on but the evidence count and the stale rule.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_elements,
    render_group,
    render_row_window,
    restamp,
)
from nora.domain.culture import Episode, RoomMessage
from nora.domain.profile import (
    PROFILE_HKT,
    Account,
    ProfileCitable,
    Said,
    handles_for,
    identity_text,
)
from nora.domain.profile_inputs import PersonSourceRevision
from nora.domain.prompt import age, escape
from nora.language.character import (
    CharacterRoom,
    _caption,
    _Shown,
    _shows,
    _within_budget,
    room_cultures_block,
)
from nora.language.culture import CULTURE_TOOLS
from nora.language.profile import (
    ProfilePersonAdaptor,
    _Culture,
    _line_attrs,
    _Profile,
    _Reads,
    _Run,
    _said,
)
from nora.obs.metrics import Metrics

__all__ = [
    "PROFILE_COLLECTIVE_ADAPTOR_NAMES",
    "PROFILE_NORA_ADAPTOR_NAME",
    "PROFILE_ROOM_ADAPTOR_NAME",
    "PROFILE_UNIT_ADAPTOR_NAME",
    "CollectiveEvidence",
    "CollectiveKey",
    "ProfileCollectiveAdaptor",
]

PROFILE_ROOM_ADAPTOR_NAME = "profile-room"
PROFILE_UNIT_ADAPTOR_NAME = "profile-unit"
PROFILE_NORA_ADAPTOR_NAME = "profile-nora"
PROFILE_COLLECTIVE_ADAPTOR_NAMES = (
    PROFILE_ROOM_ADAPTOR_NAME,
    PROFILE_UNIT_ADAPTOR_NAME,
    PROFILE_NORA_ADAPTOR_NAME,
)

#: `inputs` holds six read sites (including the conditional Profile room read):
#: the account's revision, its lines, the
#: previous prose head, the rooms' cultures in one gather, cited rows in one
#: read); `commit`'s one write is the parent's, counted in its own
#: declaration.
COLLECTIVE_STORE_CALLS_PER_TICK = 6

# ponytail: 60,000 rendered characters across ALL sources, three person-source
# allowances shared by the collective (~50k tokens for mixed Cantonese/English),
# never multiplied by members; use token-counted selection if live usage needs it.
COLLECTIVE_SOURCE_CHARS = 60_000

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("about_entity", 5, "turn"),
    ("profile_lines", 10, "turn"),
    ("previous_character", 20, "turn"),
    ("profile_messages", 40, "turn"),
    ("profile_episodes", 50, "turn"),
    ("social_items", 55, "turn"),
    ("profile_source_revisions", 60, "turn"),
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

    async def profile_revision_count(self, *, keys: Sequence[str]) -> int: ...

    async def profile_revisions_after(
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
        source_inputs: Any = None,
        members: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._source_inputs = source_inputs
        self._members = members or {}
        self._store = store
        self._keys = keys
        self._kind = kind
        self._render = render
        self._held: dict[str, int] = {}
        # Culture revision counts and Profile revision sums are different clocks.
        # Keep the legacy row for rollback; neither counter can seed the other.
        self.watermark_name = "profile-unit:profile" if kind == "unit" and render else None

    def held(self, key: str) -> int | None:
        return self._held.get(key)

    async def count(self, *, key: str, after: int) -> int:
        ck = self._keys[key]
        if self._kind == "unit":
            return max(
                0,
                await (
                    self._store.profile_revision_count(keys=[r.key for r in ck.rooms])
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
        batch, cursor = await self._newest(key=key, after=after, limit=limit)
        extra: list[Any] = []
        if self._source_inputs is not None:
            ck = self._keys[key]
            for person in self._members.get(key, ()):
                extra.extend(await self._source_inputs.rollup(key=key, unit=ck.unit,
                    rooms=[(r.platform, r.key) for r in ck.rooms], person=person, limit=limit))
                extra.extend(await self._source_inputs.social(key=key, persons=(person,), limit=6))
        return [*batch, *extra], cursor

    async def _newest(self, *, key: str, after: int, limit: int) -> tuple[Sequence[Any], int]:
        ck = self._keys[key]
        self._held[key] = after
        if self._kind == "unit":
            if self._render:
                return await self._store.profile_revisions_after(
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
        self._handed = getattr(self, "_handed", {})
        self._handed[key] = tuple(x for x in items if isinstance(x, PersonSourceRevision))

    async def settle(self, *, key: str) -> None:
        if self._source_inputs is not None:
            await self._source_inputs.settle(key=key,
                revisions=getattr(self, "_handed", {}).pop(key, ()))

    def forget(self, key: str) -> None:
        getattr(self, "_handed", {}).pop(key, None)

    def gave_up(self, key: str) -> None:
        pass


class ProfileCollectiveAdaptor(ProfilePersonAdaptor):
    """The same atomic Profile session, with each collective's own evidence."""

    name = PROFILE_ROOM_ADAPTOR_NAME
    objective = PROFILE_ROOM_ADAPTOR_NAME
    store_calls_per_tick = COLLECTIVE_STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        kind: Literal["room", "unit", "nora"],
        profile: _Profile,
        culture: _Culture,
        evidence: CollectiveEvidence,
        reads: _CollectiveReads,
        keys: Mapping[str, CollectiveKey],
        name_of: Callable[[str | None], str],
        clock_ms: Callable[[], int],
        metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
        render: bool = False,
        media_join: Any = None,
        screen: Callable[[str], str | None] | None = None,
        coordinate_screen: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(
            profile=profile,
            culture=culture,
            evidence=evidence,
            reads=reads,
            keys={},
            name_of=name_of,
            clock_ms=clock_ms,
            metrics=metrics,
            clock=clock,
            render=render, media_join=media_join, screen=screen,
            coordinate_screen=coordinate_screen,
        )
        # Re-stated, not re-read: the store-calls walk
        # (tests/architecture/test_dream_store_calls_are_declared.py) reads a
        # class's collaborators off THIS class's `__init__` assignments, and
        # the parent's are invisible to it.
        self._profile = profile
        self._render = render
        self._culture = culture
        self._reads = reads
        self._collective_keys = keys
        self._population = tuple(sorted(keys))
        self._kind = kind
        self.name = self.objective = f"profile-{kind}"
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
        revision, settled_at = await self._profile.account_state(key=key)
        heads = await self._profile.account_heads(key=key)
        allowed = frozenset(r.key for r in ck.rooms)
        revisions = [x for x in batch if isinstance(x, PersonSourceRevision)]
        direct_ids = {m.row.id for m in batch if isinstance(m, RoomMessage)}
        roots = [m for x in revisions for m in x.roots]
        batch = [*batch, *roots]
        social = [x for x in revisions if x.social is not None]
        messages = [m for m in batch if isinstance(m, RoomMessage)
                    and m.room in allowed and not m.row.is_nora]
        episodes = [e for e in batch if isinstance(e, Episode) and e.room in allowed
                    and e.outcome not in ("open", "unknown") and e.is_evidence]
        if not (messages or episodes or revisions or (self._kind == "unit" and batch)):
            return ""
        # THE BACKFILL (35 §4), the person session's rule: until a session
        # first settles the account it is handed the prose head under the same
        # key (the room's culture, the unit's or Nora's Character account) and
        # the rows it cited, once.
        previous = await self._culture.head(key=key) if settled_at is None else None
        cultures = await asyncio.gather(
            *(
                (
                    self._profile.room_context(key=r.key)
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
        shown, social_block = _within_budget([
            _Shown(item=r.social, caption=_caption(r.social.caption, person=r.subject or "",
                owners=self._handle_owners), counts=r.counts,
                shows=_shows(r.social, screen=self._screen, render_chars=4000,
                    metrics=self._metrics, person=r.subject or "", owners=self._handle_owners)
                if self._screen is not None else None)
            for r in social if r.social is not None], self._clock())
        shown_ids = {(x.item.network, x.item.item_id) for x in shown}
        revisions = [r for r in revisions if r.social is None
                     or (r.social.network, r.social.item_id) in shown_ids]
        if self._media_join is not None:
            enriched = await self._media_join.enrich(tuple(m.row for m in messages), path="dreamer")
            messages = [replace(m, row=row) for m, row in zip(messages, enriched, strict=True)]
        now = self._clock()
        handle_of = {i: h for h, i in handles_for(heads).items()}
        labels = {r.key: r.label for r in ck.rooms}

        def message_block(rows: Sequence[RoomMessage]) -> ContextBlock | None:
            if not rows:
                return None
            return ContextBlock(
                name="profile_messages", **_at("profile_messages"),
                body=render_row_window(
                    sorted(rows, key=lambda m: (m.row.ts, m.row.id)),
                    row_id=lambda m: m.row.id,
                    attrs=lambda m: (
                        ("from", self._name_of(m.row.person_id)),
                        ("room", labels.get(m.room) or "unnamed room"),
                        ("age", age(m.row.ts, now)),
                        ("source_at", datetime.fromtimestamp(
                            m.row.ts / 1000, PROFILE_HKT).isoformat()),
                    ),
                    body=lambda m: m.row.body or "",
                ),
            )

        def source_block(rows: Sequence[PersonSourceRevision]) -> ContextBlock:
            return ContextBlock(name="profile_source_revisions",
                **_at("profile_source_revisions"),
                body=escape(json.dumps([{"source": r.source, "cite": r.citable,
                    "from": self._name_of(r.subject) if r.subject else "computed",
                    "as_of": r.as_of, "from_time": r.effective_from,
                    "to_time": r.effective_until, "properties": dict(r.properties)}
                    for r in rows], ensure_ascii=False)))

        # The person writer's whole-source selection, after the media join:
        # measure what is actually shown (including escaping and metadata).
        # Repeated roots/block wrappers are charged per source, conservatively.
        joined = {m.row.id: m for m in messages}
        selected: list[PersonSourceRevision] = []
        used = 0
        for candidate in revisions:
            blocks = [source_block([candidate]), message_block([
                joined[m.row.id] for m in candidate.roots if m.row.id in joined])]
            if candidate.social is not None:
                _, item_block = _within_budget([x for x in shown if (
                    x.item.network, x.item.item_id) == (
                        candidate.social.network, candidate.social.item_id)], now)
                blocks.append(item_block)
            size = sum(len(b.render()) + 2 for b in blocks if b is not None)
            if used + size > COLLECTIVE_SOURCE_CHARS:
                continue
            used += size
            selected.append(candidate)
        revisions = selected
        retained = direct_ids | {m.row.id for m in old}
        retained.update(m.row.id for r in revisions for m in r.roots)
        messages = [m for m in messages if m.row.id in retained]
        social = [r for r in revisions if r.social is not None]
        selected_social = {(r.social.network, r.social.item_id)
                           for r in social if r.social is not None}
        _, social_block = _within_budget([x for x in shown
            if (x.item.network, x.item.item_id) in selected_social], now)
        self.evidence.handed(key, revisions)
        said = {m.row.id: _said(m) for m in [*messages, *replies]}
        for episode in episodes:
            for reply in episode.replies:
                if not reply.is_nora:
                    said[reply.id] = Said(reply.ts, reply.person_id, episode.room,
                        occasion=f"response:{episode.send_id}", medium="act")
        self._runs[key] = _Run(
            account=Account(key=key, entity_kind=self._kind, unit=ck.unit),
            citable=ProfileCitable(
                messages=said,
                sources={c: source for r in revisions for c, source in r.evidence},
                social={f"{x.social.network}:{x.social.item_id}": Said(
                    int(x.social.taken_at.timestamp() * 1000),
                    x.subject, None, occasion=f"social:{x.social.network}:{x.social.item_id}",
                    medium="social") for x in social if x.social is not None},
                social_subjects=frozenset(x.subject for x in social if x.subject is not None),
                episodes={e.send_id: Said(stamps[e.send_id], None, e.room,
                          occasion=f"response:{e.send_id}", medium="act") for e in episodes},
                listed_rooms=allowed,
            ),
            revision=revision, heads=heads, batch_start=self.evidence.held(key),
            tellers={self._name_of(s.person_id).casefold(): s.person_id
                     for s in said.values() if s.person_id is not None},
        )
        identity_names = {s.person_id for s in said.values()}
        def identity_name(pid: str) -> str:
            return self._name_of(pid) if pid in identity_names else "someone"
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
                name="profile_lines",
                **_at("profile_lines"),
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
            message_block(messages),
            None
            if not episodes
            else ContextBlock(
                name="profile_episodes",
                **_at("profile_episodes"),
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
        if social_block is not None:
            parts.append(restamp(social_block, _ROW))
        if revisions:
            parts.append(source_block(revisions))
        return self._checked_input(key, assemble([p for p in parts if p is not None], sep="\n\n"))
