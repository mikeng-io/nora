"""A bounded, in-memory LRU of MiMo descriptions keyed by content hash (DEC-56),
a READ-THROUGH in front of the durable `MediaDescriptionStore`.

The sha256 IS the content, so a description is valid forever for that hash — a
cache hit is not staleness, it is the same bytes. This holds the RAW description
at BOTH tiers (the identifier screen runs on every SERVE, in the tool, never at
store time — so a cache hit, at either tier, can never bypass it).

Two tiers, not one, and neither is redundant with the other. The in-memory LRU
saves a Postgres round trip on the hot path and keeps a store outage free
within one process; it is the whole cache when `store` is `None`. The durable
table is what makes a RESTART cheap: the LRU alone loses every description on
a deploy, and the next sighting of every image would pay MiMo again. Nora
restarts often; stickers repeat over weeks. `aget`/`aput` check memory first
and warm it on a durable hit; the synchronous `get`/`put` serve the
memory-only tier.

`aput` writes `platform=""`/`token=""` when called from `media_fetch`: that
tool describes an image a human pointed at by handle, and there is no
platform-side sticker IDENTITY for it — only content — so only the content
level (`MediaDescriptionStore.describe`) is written, never a fabricated
identity. `SqlMediaDescriptionStore.remember` guards its `sticker_index` write
on a non-empty token for exactly this reason: without the guard, every
`media_fetch` write would upsert the SAME junk row keyed `("", "")`, carrying
whichever hash happened to write last.

Owned by `ToolRegistry` — the one thing the composition root builds once per
process — for the same reason the transit catalogue is: a per-tag cache would
never hit, and a module-level singleton is what CLAUDE.md's test-seam policy
forbids.

Soundness across conversations: a hit in another room still required that room
to hold the same bytes (the handle resolves in ITS scope first), and the
description is a pure function of content, so a shared hit leaks nothing about
where else the image appeared.
"""

from __future__ import annotations

from collections import OrderedDict

from nora.domain.media import MediaBlob
from nora.domain.ports import MediaDescriptionStore


class VisionCache:
    """sha256 → raw MiMo description, bounded to `maxsize` most-recent entries,
    read-through to `store` when memory misses."""

    def __init__(
        self, *, maxsize: int = 256, store: MediaDescriptionStore | None = None,
    ) -> None:
        self._maxsize = max(1, maxsize)
        self._data: OrderedDict[str, str] = OrderedDict()
        self._store = store

    def get(self, key: str) -> str | None:
        """The stored description for `key`, or None. A hit is promoted to
        most-recently-used so the bound evicts genuinely cold entries."""
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: str, value: str) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    async def aget(self, key: str) -> str | None:
        """Memory first, then the durable table, warming memory on the way back.

        The in-memory tier is not redundant with the table: it saves a Postgres
        round trip on the hot path, and it is what keeps a store outage from
        costing anything within one process. The table is what makes a RESTART
        cheap: the LRU alone loses every description on a deploy.
        """
        hit = self.get(key)
        if hit is not None or self._store is None:
            return hit
        durable = await self._store.describe(sha256=key)
        if durable is not None:
            self.put(key, durable)
        return durable

    async def aput(self, key: str, value: str, *, model: str) -> None:
        """Write both tiers. The RAW description is stored, here as in memory —
        the identifier screen runs on every SERVE, in the tool, so a hit at
        either tier can never bypass it.

        `platform=""`/`token=""`: see the module docstring — this cache has no
        sticker identity to offer, only content, and `remember` degrades that
        into "no index write" rather than a junk row.
        """
        self.put(key, value)
        if self._store is not None:
            await self._store.remember(
                platform="", token="",
                blob=MediaBlob(sha256=key, media_type="", size=0),
                description=value, model=model,
            )

    def __len__(self) -> int:
        return len(self._data)
