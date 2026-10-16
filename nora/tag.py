"""Trigger matching (R-11) and per-conversation debounce (R-9).

Firing is about *tagging intent*, not string presence: a trigger quoted as code is
not a tag.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Callable, Coroutine, Hashable
from typing import Any, Protocol

log = logging.getLogger("nora.tag")

# Zero-width and bidi-control characters used to smuggle a trigger past a naive match.
_INVISIBLE = dict.fromkeys(
    [*range(0x200B, 0x2010), *range(0x202A, 0x202F), 0x2060, 0xFEFF], None
)

_FENCED = re.compile(r"```.*?```|```.*\Z", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_WORD = re.compile(r"[0-9A-Za-z_]")


def normalize(body: str) -> str:
    """NFKC, then drop zero-width and bidi-control characters."""
    return unicodedata.normalize("NFKC", body).translate(_INVISIBLE)


def strip_code_spans(body: str) -> str:
    """Remove fenced blocks then inline spans. Unclosed fences eat the tail."""
    return _INLINE_CODE.sub(" ", _FENCED.sub(" ", body))


def is_tagged(body: str, trigger: str) -> bool:
    if not body or not trigger:
        return False
    text = strip_code_spans(normalize(body))
    pattern = re.compile(re.escape(normalize(trigger)), re.IGNORECASE)
    for match in pattern.finditer(text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if _WORD.match(before) or _WORD.match(after):
            continue                       # a word character on either side is not a tag
        return True
    return False


class DebounceStore(Protocol):
    """The three verbs `Debounce` needs from `nora.infra.sql.tag_debounce`.

    A STRUCTURAL shape declared here rather than an import, so this module keeps
    importing nothing but the standard library: it is a leaf, and the engagement
    path's rate limiter must not depend on SQLAlchemy to run.
    """

    async def read_all(self) -> list[tuple[str, str, int]]: ...

    async def prune_before(self, *, before_ms: int) -> int: ...

    async def record_allowed(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None: ...


def _room(key: Hashable) -> tuple[str, str] | None:
    """The `(platform, conversation_id)` a debounce key names, or `None`.

    Production's key is exactly that two-tuple
    (`nora.engagement.stages.admission.run`). `ConversationTarget`, the shape
    the unit tests key with, is a frozen dataclass, not a tuple, and so has no
    durable form here: it stays in memory only. Returning `None` rather than
    guessing is the point; a key coerced through `str()` would round-trip to
    something that is not the key it came from, and hydration would then
    restore a window against a room nobody can name.
    """
    if (
        type(key) is tuple
        and len(key) == 2
        and isinstance(key[0], str)
        and isinstance(key[1], str)
    ):
        return key[0], key[1]
    return None


class Debounce:
    """R-9: a second tag for the same conversation inside the window is dropped.

    The clock is injected so tests are deterministic and never sleep. A blocked tag
    does NOT reset the window — otherwise a burst would starve the conversation.

    **`store` is OPTIONAL; `None` is the default for every caller that is not
    the composition root.** With it absent this class is an in-memory dict that
    a restart empties, which is what the tests and every offline/eval wiring
    get. Supplying one makes `allow`'s answer survive a force-recreate
    (migration `0056`).

    **The write goes through `spawn`, because `allow` may not await.** Its
    caller chain is `EngagementPipeline.run` -> `admission.run`, and that
    module's docstring pins `run()` as a pure decision function that does no
    I/O: the QUEUED verdict and the enqueue that follows it must have no await
    between them, or two concurrent tags read the same queue depth and FIFO
    order stops being arrival order. So the row is written fire-and-forget on
    the loop, the same shape `nora.ingest.stages.memory_feed` uses off the
    listener's path. `spawn` is REQUIRED when `store` is given: a store with
    nowhere to spawn would be a fourth wiring state that looks configured and
    persists nothing.

    **The clock is `time.time`, not `time.monotonic`.** A monotonic epoch is
    arbitrary per process, so a persisted monotonic value is not a time to
    whatever reads it back. Every comparison here is a DIFFERENCE of two
    readings of the same clock, so the choice changes no answer; it is what
    makes the stored value meaningful.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.time,
        store: DebounceStore | None = None,
        spawn: Callable[[Coroutine[Any, Any, None]], Any] | None = None,
    ) -> None:
        if store is not None and spawn is None:
            raise ValueError(
                "Debounce(store=...) needs a spawn: allow() is synchronous by "
                "design (nora.engagement.stages.admission) and cannot await the "
                "write itself"
            )
        self._interval = float(min_interval_seconds)
        self._clock = clock
        self._store = store
        self._spawn = spawn
        self._last: dict[Hashable, float] = {}

    async def hydrate(self) -> int:
        """Load the surviving windows from the store; returns how many.

        Called ONCE, at wiring time, before any adapter is listening; `allow`
        is synchronous and cannot do this itself.

        It PRUNES first, with a cutoff computed from this instance's own
        interval: a row older than `min_interval_seconds` cannot change
        `allow()`'s answer again, so dropping it is lossless by arithmetic
        rather than by retention policy, and it is what bounds the table
        without a sweep loop of its own.

        A no-op with no store — never an error. `hydrate` is safe to call on
        the in-memory shape so the composition root has one call site rather
        than a branch.
        """
        if self._store is None:
            return 0
        now_ms = int(self._clock() * 1000)
        await self._store.prune_before(before_ms=now_ms - int(self._interval * 1000))
        rows = await self._store.read_all()
        for platform, conversation_id, at_ms in rows:
            self._last[(platform, conversation_id)] = at_ms / 1000.0
        return len(rows)

    def allow(self, key: Hashable) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and (now - last) < self._interval:
            return False
        self._last[key] = now
        self._persist(key, now)
        return True

    def _persist(self, key: Hashable, now: float) -> None:
        """Mirror an ALLOWED tag into the store, off this call's path.

        Guarded end to end: a rate limiter that raised into `admission.run`
        would refuse a tag because a write failed, which is a worse outcome
        than the one persistence exists to prevent. A key with no durable form
        (`_room`) is kept in memory and nowhere else.
        """
        if self._store is None or self._spawn is None:
            return
        room = _room(key)
        if room is None:
            return
        coro = self._store.record_allowed(
            platform=room[0], conversation_id=room[1], at_ms=int(now * 1000),
        )
        try:
            self._spawn(coro)
        except Exception:  # a failed mirror must never refuse a tag
            # CLOSED, not dropped: an un-awaited coroutine object emits a
            # RuntimeWarning at collection, and a warning about a write nobody
            # asked for is how a real one gets tuned out.
            coro.close()
            log.debug("debounce state not persisted for %r", room, exc_info=True)

    def sweep(self, older_than: float) -> int:
        """Drop entries last seen more than `older_than` seconds ago (by the
        injected clock) and return how many were dropped.

        `_last` is otherwise unbounded — every distinct (platform, conversation_id)
        key it ever sees stays forever, so it grows with platforms x conversations.
        This is the hook `app/runtime.py` calls on an interval to keep it bounded;
        `Debounce` itself never calls this.
        """
        now = self._clock()
        stale = [key for key, last in self._last.items() if now - last > older_than]
        for key in stale:
            del self._last[key]
        return len(stale)
