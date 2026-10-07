"""The one-time move of person accounts from per-room keys to per-unit keys
(design/self-growth/10-character.md §3, C2b).

Until C2b a person's account was keyed per ROOM, `{room primary_id}#character:
{person}`, written by each room's fold (DEC-278). From C2b it is one account per
(unit, person), `character_key(unit, person)`, written by that person's own
session. So that nobody loses an account at the switch, each unit key with no
head yet is given, as its FIRST revision, a copy of the newest per-room
revision of that person among the unit's rooms: same body, same `written_at`
(so its rendered age stays true), same `instruction_sha256` and `model`, and
`established_by = "moved:<source revision id>"`, which names the row it came
from. Its parent is `None`: a unit key's chain starts here. The per-room keys
and their chains stay where they are (nothing in the culture store retires,
DEC-183); nothing reads them after this.

**Code at startup, not an alembic migration**, because which rooms make a unit
is `policy.toml` (DEC-105), which the database does not hold: a migration
cannot know that room A and room B are one unit. Idempotent, like
`nora.app.runtime.seed_watermarks`: a unit key that already has a head is
never touched, so the second start moves nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from nora.domain.culture import CultureRevision, character_key
from nora.language.character import PersonKey

log = logging.getLogger(__name__)

__all__ = ["seed_unit_person_heads"]


class _CultureStore(Protocol):
    async def head(self, *, key: str) -> CultureRevision | None: ...

    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = ..., model: str | None = ...,
    ) -> CultureRevision: ...


async def seed_unit_person_heads(
    *, culture: _CultureStore, keys: Mapping[str, PersonKey],
) -> int:
    """Move each person's newest per-room account to their unit key, once.
    Returns how many unit keys were given a first revision."""
    moved = 0
    for key, pk in sorted(keys.items()):
        if await culture.head(key=key) is not None:
            continue
        found: list[CultureRevision] = []
        for room in pk.rooms:
            # The pre-C2b key: the same format with the ROOM in the unit's place.
            old = await culture.head(key=character_key(room.key, pk.person.person_id))
            if old is not None:
                found.append(old)
        if not found:
            continue
        source = max(found, key=lambda r: (r.written_at, r.id))
        await culture.append(
            key=key, body=source.body, written_at=source.written_at,
            established_by=f"moved:{source.id}",
            instruction_sha256=source.instruction_sha256, model=source.model,
        )
        moved += 1
        log.info(
            "character: moved %s's account to unit %s from revision %s",
            pk.person.person_id, pk.unit, source.id,
        )
    return moved
