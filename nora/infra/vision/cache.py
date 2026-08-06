"""A bounded, in-memory LRU of MiMo descriptions keyed by content hash (DEC-56).

The sha256 IS the content, so a description is valid forever for that hash — a
cache hit is not staleness, it is the same bytes. This holds the RAW description
(the identifier screen runs on every SERVE, in the tool, never at store time —
so a cache hit can never bypass it).

In-memory and per process; persistence is deferred, because a restart
re-describing a re-referenced image is one MiMo call, not a correctness bug. It
is owned by `ToolRegistry` — the one thing the composition root builds once per
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


class VisionCache:
    """sha256 → raw MiMo description, bounded to `maxsize` most-recent entries."""

    def __init__(self, *, maxsize: int = 256) -> None:
        self._maxsize = max(1, maxsize)
        self._data: OrderedDict[str, str] = OrderedDict()

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

    def __len__(self) -> int:
        return len(self._data)
