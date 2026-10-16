"""`StoryMediaExpiry` — a story frame's bytes deleted at their bound
(design/cognition-loop/22-social-stream.md §5; 70-migration.md §9).

**What goes, and when** (`nora.domain.social_stream.bytes_due`). Only a story
frame's media bytes: a post's and a reel's are never read here. They go
`[social] story_media_keep_days` after the frame's `expires_at` once its
perception and its embedding both exist, or `story_media_keep_max_days` after
it whatever exists; the frame then stays unembedded, which is counted, not an
error. The rows, the description perception wrote (`media_objects`) and any
vector stay.

**What is shared stays.** One address can be named by many rows: a frame a
member also sent to a room, a post that repeats it, the computer audit. Bytes
go only when nothing names them but the frames being let go in the same pass
(`nora.infra.sql.blob_refs.held_elsewhere`), asked again for each address
just before its deletion; a frame whose bytes are held elsewhere is asked
again on the next tick.

**A writer between the check and the deletion.** A room message carrying the
same bytes stores them (`put`) before it writes the row naming them, so a
check can see no row while the bytes are already promised. `BlobStore.delete`
therefore keeps bytes put within `PUT_GRACE_SECONDS` by the store's own clock,
a `put` of bytes already held counting as a put, and the check and the
removal are one step against `put` in each store. A writer whose row lands
more than an hour after its `put` is the ceiling of this.

**Delete, then tombstone.** The tombstone is written only once the store
confirms the bytes are gone: `deleted`, or `absent` (a restart between the two
finds the frame again and its deletion answers that). Bytes it kept, or a
deletion that failed, write nothing and are tried on the next tick. A
tombstone is about the rows recorded before its `deleted_at`, read off the
database's clock before the deletion: bytes stored again afterwards (a member
posts the same picture) belong to the new rows, and a story frame
recorded since is deleted at its own bound. Every fact the pass decides on is
a row, so a restart neither deletes twice nor misses a frame whose bound
passed while it was down.

Off unless `[social] story_expiry_enabled`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from nora.domain.ports import BlobStore
from nora.domain.social_stream import ByteDeletionReason, bytes_due
from nora.infra.sql.social_stream import ExpiringFrame, SqlSocialStreamStore
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.social_expiry")

#: Bytes put this recently are kept: the row naming them may not be written
#: yet. Ingest writes its row within seconds of the `put`.
PUT_GRACE_SECONDS = 3600.0


class StoryMediaExpiry:
    def __init__(
        self,
        *,
        store: SqlSocialStreamStore,
        blobs: BlobStore,
        keep_days: int,
        max_days: int,
        tick_seconds: float,
        metrics: Metrics,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._blobs = blobs
        self._keep_days = keep_days
        self._max_days = max_days
        self._tick_seconds = tick_seconds
        self._metrics = metrics
        self._clock = clock
        self._sleep = sleep

    async def run(self) -> None:
        log.info(
            "story media expiry started: bytes kept %d days past expiry once embedded, "
            "%d at most", self._keep_days, self._max_days,
        )
        while True:
            await self._sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("story media expiry tick failed")
                self._metrics.inc("nora_errors_total", stage="social_story_expiry")

    async def tick(self) -> None:
        """Delete the bytes of every story frame past its bound, and tombstone
        them."""
        now = self._clock()
        frames = await self._store.frames_past_bound(
            before=now - timedelta(days=self._keep_days),
        )
        due: list[tuple[ExpiringFrame, ByteDeletionReason, bool]] = []
        for frame in frames:
            # ponytail: the memory index projects no social item yet, so no
            # frame's media has a vector and every frame waits for the hard
            # limit. Read the frame's vector off the index here once it does.
            embedded = False
            reason = bytes_due(
                expires_at=frame.expires_at, now=now, perceived=frame.perceived,
                embedded=embedded, keep_days=self._keep_days, max_days=self._max_days,
            )
            if reason is not None:
                due.append((frame, reason, embedded))
        if not due:
            return
        reasons: dict[str, ByteDeletionReason] = {}
        for frame, reason, _ in due:
            for sha in frame.media:
                reasons.setdefault(sha, reason)
        letting_go = [(f.network, f.item_id) for f, _, _ in due]
        went: set[str] = set()
        for sha, reason in reasons.items():
            if await self._delete(sha, reason, letting_go):
                went.add(sha)
        for frame, reason, embedded in due:
            if reason == "hard_limit" and not embedded and went.intersection(frame.media):
                self._metrics.inc("nora_social_story_unembedded_total")

    async def _delete(
        self, sha: str, reason: ByteDeletionReason, letting_go: list[tuple[str, str]],
    ) -> bool:
        """Delete one address's bytes, if nothing else names them, and
        tombstone it once the store confirms they are gone; True when this
        tick wrote its tombstone."""
        if await self._store.held_elsewhere(shas=[sha], letting_go=letting_go):
            return False
        # Before the deletion: a row recorded after this instant may have
        # stored the bytes again, and the tombstone must not cover it.
        deleted_at = await self._store.now()
        try:
            done = await self._blobs.delete(key=sha, idle_seconds=PUT_GRACE_SECONDS)
        except Exception:
            log.exception("story media expiry: the blob store refused a deletion")
            self._metrics.inc("nora_errors_total", stage="social_story_expiry")
            return False
        if done == "kept":
            return False
        await self._store.record_tombstone(sha256=sha, reason=reason, deleted_at=deleted_at)
        self._metrics.inc("nora_social_story_bytes_deleted_total", reason=reason)
        return True
