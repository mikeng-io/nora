"""Bounded queue-depth and retention reads (design/platform-bridges/04
§aggregates).

The numbers and the rule that keeps them honest:

- **waiting** — entries the consumer group has NEVER delivered: an `XRANGE`
  read STRICTLY AFTER the group's `last-delivered-id`. Not `XLEN`: stream
  length retains ACKed and failed_retained entries, so length reads history
  as backlog — the exact confusion this module exists to prevent.
- **pending** — the group's PEL (`XPENDING`): delivered, never ACKed. Read
  separately from waiting and REPORTED separately; the two sets are disjoint
  (an entry is either never delivered, or delivered-but-unacked), so they are
  never summed into one number. Its age is the server's own IDLE evidence —
  `time_since_delivered` from the extended XPENDING entries — never the
  stream-id's enqueue time: an entry enqueued yesterday and first delivered
  now is a FRESH pending, not a 24-hour one.
- **deferred** — `bridge_work` rows still owning unfinished obligations.
  Redis empty does not mean all work finished; an ACKed event's transferred
  obligations are this count, per actual row state.
- **retained** — `failed_retained`/`uncertain` work rows and `uncertain`
  actions: the visible, requeueable record of what did not complete.

Failure posture (R-27): the result is exactly `ok` / `nothing_found` /
`unavailable` — degradation is METADATA, not a fourth outcome. An aggregate
that cannot be measured fully is `unavailable`: a failed stream, a capped or
non-coherent scan, or a failed DB section. Successful sections stay visible
in `streams` and the `deferred`/`retained`/`uncertain_actions` fields —
`partial=True` marks that case — but the AGGREGATE fields stay `None` and no
backlog gauge is published: a failed read is an absent gauge, never a fresh
zero or a lower bound reported as exact. `nora_bridge_inspections_total`
always records that an inspection ran. For a coherent sample the group's
last-delivered position is rechecked after scanning and retried once; a
still-moving group reads `coherent=False` (and therefore `unavailable`)
rather than a confidently stale number.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from .telemetry import (
    ACTION_OPS,
    ACTION_OUTCOMES,
    INSPECTION_RESULTS,
    LIFECYCLE_EVENTS,
    WORK_OUTCOMES,
    InspectionResult,
)

log = logging.getLogger("nora.platform.bridge.inspection")

CONSUMER_GROUP = "nora-v1"
_STREAM_PREFIX = "nq:in:"

#: `bridge_work` states that mean unfinished obligations (deferred depth) vs
#: terminal-but-retained evidence (failure retention). Terminal states
#: (`done`, `intentional_skip`, `cancelled`) are neither.
_DEFERRED_STATES = frozenset({"pending", "running", "retryable_failure"})
_RETAINED_STATES = frozenset({"failed_retained", "uncertain"})


def _entry_ms(stream_id: str) -> int:
    """The enqueue-time millis a Redis stream id carries (`<ms>-<seq>`)."""
    return int(str(stream_id).split("-", 1)[0])


@dataclass(frozen=True)
class StreamSnapshot:
    """One stream's queue reading. `error` set ⇒ the numeric fields are
    `None`, not zero."""
    stream: str
    waiting: int | None = None
    waiting_oldest_ms: int | None = None
    pending: int | None = None
    pending_oldest_ms: int | None = None
    coherent: bool = True
    truncated: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DbSnapshot:
    """The `bridge_work`/`bridge_action` reading. `error` set ⇒ `None`s."""
    deferred: dict[str, int] | None = None
    retained: dict[str, int] | None = None
    uncertain_actions: int | None = None
    oldest_due_seconds: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class QueueInspection:
    """The verdict plus the degradation metadata that keeps it honest.

    `status` is the R-27 read of the SNAPSHOT — never of the queue:
    `nothing_found` means "checked, empty", `unavailable` means "couldn't
    check fully". When `unavailable`, the aggregate fields are `None`
    (unmeasured, never zero) while `partial` says whether ANY section
    measured — its observations remain in `streams` and the db fields.
    """
    status: InspectionResult
    platform: str
    waiting_entries: int | None = None
    waiting_oldest_ms: int | None = None
    pending_entries: int | None = None
    pending_oldest_ms: int | None = None
    deferred: dict[str, int] = field(default_factory=dict)
    retained: dict[str, int] = field(default_factory=dict)
    uncertain_actions: int | None = None
    oldest_due_seconds: float | None = None
    partial: bool = False               # degraded, but ≥1 section measured
    coherent: bool = True
    truncated: bool = False             # a bounded scan hit its cap
    db_status: str = "skipped"          # ok | skipped | unavailable
    detail: str = ""
    streams: tuple[StreamSnapshot, ...] = ()


class QueueInspector:
    """Paged, coherence-checked reads over Redis Streams + the durable
    `bridge_work`/`bridge_action` rows.

    `redis` is the app's async Redis client (duck-typed, redis-py shaped:
    `xinfo_groups`, `xrange`, `xpending`, `xpending_range`, `time`); `engine`
    is the SQLAlchemy async engine reaching the `bridge_*` schema —
    injected, never constructed here.
    """

    def __init__(self, redis: Any, *, engine: Any = None,
                 metrics: Any = None, group: str = CONSUMER_GROUP,
                 page_size: int = 500, max_pages: int = 10) -> None:
        self._redis = redis
        self._engine = engine
        self._metrics = metrics
        self._group = group
        self._page = page_size
        self._max_pages = max_pages

    async def inspect(self, streams: Sequence[str], *, platform: str = ""
                      ) -> QueueInspection:
        """Read every named stream plus the DB section; publish what was
        actually measured and count the inspection itself."""
        snaps: list[StreamSnapshot] = []
        for stream in streams:
            snaps.append(await self._inspect_stream(stream))
        db = await self._db_depth(platform)

        stream_errors = [s for s in snaps if s.error]
        incoherent = any(not s.coherent for s in snaps)
        truncated = any(s.truncated for s in snaps)
        degraded = bool(stream_errors) or incoherent or truncated \
            or bool(db.error)
        # A section that measured keeps its observation (streams, db fields)
        # — `partial` distinguishes "degraded with data" from "degraded and
        # blind". The aggregates below stay None either way: an exact sum
        # exists only when EVERY section measured.
        partial = degraded and (
            any(not s.error for s in snaps)
            or (self._engine is not None and not db.error))

        if degraded:
            status: InspectionResult = "unavailable"
            waiting = pending = waiting_oldest = pending_oldest = None
        else:
            waiting = sum(s.waiting or 0 for s in snaps)
            pending = sum(s.pending or 0 for s in snaps)
            ages_w = [s.waiting_oldest_ms for s in snaps
                      if s.waiting_oldest_ms is not None]
            ages_p = [s.pending_oldest_ms for s in snaps
                      if s.pending_oldest_ms is not None]
            waiting_oldest = max(ages_w) if ages_w else None
            pending_oldest = max(ages_p) if ages_p else None
            # every durable obligation/failure counts toward "something
            # found" — a lone uncertain action is not nothing
            if (waiting == 0 and pending == 0 and not db.deferred
                    and not db.retained and not db.uncertain_actions):
                status = "nothing_found"
            else:
                status = "ok"

        detail = "; ".join(filter(None, [
            *(f"{s.stream}: {s.error}" for s in stream_errors),
            "db: " + db.error if db.error else "",
            "scan did not converge" if incoherent else "",
            "a bounded scan hit its cap" if truncated else "",
        ]))
        inspection = QueueInspection(
            status=status, platform=platform,
            waiting_entries=waiting, waiting_oldest_ms=waiting_oldest,
            pending_entries=pending, pending_oldest_ms=pending_oldest,
            deferred=db.deferred or {}, retained=db.retained or {},
            uncertain_actions=db.uncertain_actions,
            oldest_due_seconds=db.oldest_due_seconds,
            partial=partial, coherent=not incoherent, truncated=truncated,
            db_status=("unavailable" if db.error
                       else "ok" if self._engine is not None else "skipped"),
            detail=detail, streams=tuple(snaps))
        self._publish(inspection)
        return inspection

    # ---- Redis section ----------------------------------------------------

    async def _inspect_stream(self, stream: str) -> StreamSnapshot:
        try:
            return await self._read_stream(stream)
        except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
            # One stream's failure must not become another's data — the
            # aggregate reports unavailable, fields stay None. The TYPE name
            # only: an exception's message/stack can carry internals.
            log.debug("queue inspection of %s failed (%s)",
                      stream, type(exc).__name__)
            return StreamSnapshot(stream=stream, error=type(exc).__name__)

    async def _read_stream(self, stream: str) -> StreamSnapshot:
        now_ms = await self._now_ms()
        # No group (yet — an app-absent enqueue) or no stream: the
        # last-delivered position is "0-0", so EVERY entry is never-delivered.
        # `waiting` is the strict-after read, not XLEN — the two coincide only
        # while nothing has been delivered.
        last_delivered = await self._last_delivered(stream) or "0-0"

        waiting, oldest_waiting, wait_capped = await self._waiting(
            stream, last_delivered, now_ms)
        pending, oldest_pending, pend_capped = await self._pending(stream)
        coherent = await self._last_delivered(stream) == last_delivered
        if not coherent:
            # The group moved mid-scan: retry once for a coherent sample,
            # then report honestly rather than publish a torn read.
            last2 = await self._last_delivered(stream)
            waiting, oldest_waiting, wait_capped = await self._waiting(
                stream, last2 or "0-0", now_ms)
            pending, oldest_pending, pend_capped = await self._pending(
                stream)
            coherent = await self._last_delivered(stream) == last2
        return StreamSnapshot(
            stream=stream, waiting=waiting,
            waiting_oldest_ms=oldest_waiting, pending=pending,
            pending_oldest_ms=oldest_pending, coherent=coherent,
            truncated=wait_capped or pend_capped)

    async def _now_ms(self) -> int:
        secs, micros = await self._redis.time()
        return secs * 1000 + micros // 1000

    async def _last_delivered(self, stream: str) -> str | None:
        try:
            groups = await self._redis.xinfo_groups(stream)
        except Exception as exc:
            # NOGROUP: group not created yet; "no such key": the stream itself
            # is absent. Both are a checked-empty answer — "0-0".
            if "NOGROUP" in str(exc) or "no such key" in str(exc):
                return None
            raise
        for g in groups:
            name = g.get("name")
            if (name == self._group
                    or (isinstance(name, bytes)
                        and name.decode() == self._group)):
                lid = g.get("last-delivered-id") or g.get("last_delivered_id")
                return (lid.decode() if isinstance(lid, bytes) else lid) or "0-0"
        return None

    async def _waiting(self, stream: str, last_delivered: str,
                       now_ms: int) -> tuple[int, int | None, bool]:
        """Never-delivered entries: `XRANGE` STRICTLY AFTER the group's
        last-delivered id, paged and bounded."""
        total = 0
        oldest_ms: int | None = None
        cursor = f"({last_delivered}"
        truncated = False
        for _ in range(self._max_pages):
            entries = await self._redis.xrange(
                stream, min=cursor, max="+", count=self._page)
            if not entries:
                break
            for entry_id, _fields in entries:
                eid = entry_id.decode() if isinstance(entry_id, bytes) \
                    else entry_id
                total += 1
                if oldest_ms is None:
                    oldest_ms = now_ms - _entry_ms(eid)
                cursor = f"({eid}"
            if len(entries) < self._page:
                break
        else:
            truncated = True
        return total, oldest_ms, truncated

    async def _pending(self, stream: str
                       ) -> tuple[int, int | None, bool]:
        """The PEL: delivered, unACKed — a different set from `waiting`.

        Count comes from the XPENDING summary; the age is the server's own
        IDLE evidence — `time_since_delivered` on the extended-XPENDING
        entries — never `now - <stream id's enqueue ms>`: an entry enqueued
        yesterday and delivered one second ago is a one-second-old pending,
        and enqueue-age would lie about it by a day. The scan is bounded at
        one page; a PEL larger than that reports its exact count, keeps the
        largest scanned idle, and marks the read capped (⇒ `unavailable`)
        rather than claiming the bound is the maximum.
        """
        try:
            summary = await self._redis.xpending(stream, self._group)
        except Exception as exc:
            if "NOGROUP" in str(exc) or "no such key" in str(exc):
                return 0, None, False   # no group ⇒ no PEL — checked empty
            raise
        count = (int(summary.get("pending", 0))
                 if isinstance(summary, dict) else int(summary[0]))
        if count == 0:
            return 0, None, False
        entries = await self._redis.xpending_range(
            stream, self._group, min="-", max="+", count=self._page)
        idles = [int(e["time_since_delivered"]) for e in entries
                 if e.get("time_since_delivered") is not None]
        # `len(entries) < count` ⇒ entries beyond the page went unscanned —
        # the age seen is a lower bound on the true max idle, not the max.
        return count, (max(idles) if idles else None), len(entries) < count

    # ---- Postgres section ---------------------------------------------------

    async def _db_depth(self, platform: str) -> DbSnapshot:
        if self._engine is None:
            return DbSnapshot()
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(sa.text(
                    "SELECT state, count(*) AS n FROM bridge_work"
                    " WHERE platform = :p GROUP BY state"),
                    {"p": platform})).all()
                by_state = {r[0]: int(r[1]) for r in rows}
                oldest = (await conn.execute(sa.text(
                    "SELECT min(due_at) FROM bridge_work"
                    " WHERE platform = :p"
                    " AND state IN ('pending','running','retryable_failure')"),
                    {"p": platform})).scalar_one_or_none()
                uncertain_actions = int((await conn.execute(sa.text(
                    "SELECT count(*) FROM bridge_action"
                    " WHERE platform = :p AND state = 'uncertain'"),
                    {"p": platform})).scalar_one())
            deferred = {s: n for s, n in by_state.items()
                        if s in _DEFERRED_STATES}
            retained = {s: n for s, n in by_state.items()
                        if s in _RETAINED_STATES}
            oldest_due = None
            if oldest is not None:
                due = (oldest.replace(tzinfo=_dt.UTC)
                       if oldest.tzinfo is None else oldest)
                oldest_due = max(
                    0.0, (_dt.datetime.now(_dt.UTC) - due).total_seconds())
            return DbSnapshot(deferred=deferred, retained=retained,
                              uncertain_actions=uncertain_actions,
                              oldest_due_seconds=oldest_due)
        except Exception as exc:  # noqa: BLE001 — telemetry is never load-bearing; type name logged, message withheld
            log.debug("bridge db depth read failed (%s)",
                      type(exc).__name__)
            return DbSnapshot(error=type(exc).__name__)

    # ---- publication ---------------------------------------------------------

    def _publish(self, inspection: QueueInspection) -> None:
        """Write ONLY what was fully measured. `unavailable` publishes
        nothing but the inspection counter itself — the aggregate fields are
        `None` then anyway, so the per-field checks and the status check
        agree: an invalid backlog gauge is omitted, never reset to zero and
        never a capped lower bound passed off as exact (design 04)."""
        if self._metrics is None:
            return
        m, platform = self._metrics, inspection.platform
        if inspection.status != "unavailable":
            if inspection.waiting_entries is not None:
                m.gauge("nora_bridge_queue_waiting_entries",
                        inspection.waiting_entries, platform=platform)
            if (inspection.waiting_oldest_ms is not None
                    or inspection.waiting_entries == 0):
                m.gauge("nora_bridge_queue_waiting_oldest_seconds",
                        (inspection.waiting_oldest_ms or 0) / 1000,
                        platform=platform)
            if inspection.pending_entries is not None:
                m.gauge("nora_bridge_queue_pending_entries",
                        inspection.pending_entries, platform=platform)
            if (inspection.pending_oldest_ms is not None
                    or inspection.pending_entries == 0):
                m.gauge("nora_bridge_queue_pending_oldest_seconds",
                        (inspection.pending_oldest_ms or 0) / 1000,
                        platform=platform)
            if inspection.db_status == "ok":
                for state in sorted(_DEFERRED_STATES):
                    m.gauge("nora_bridge_deferred_work_entries",
                            inspection.deferred.get(state, 0),
                            platform=platform, state=state)
                for state in sorted(_RETAINED_STATES):
                    m.gauge("nora_bridge_retained_work_entries",
                            inspection.retained.get(state, 0),
                            platform=platform, state=state)
            if inspection.uncertain_actions is not None:
                m.gauge("nora_bridge_retained_actions",
                        inspection.uncertain_actions,
                        platform=platform, state="uncertain")
        m.inc("nora_bridge_inspections_total",
              platform=platform, result=inspection.status)


def prime(metrics: Any, platforms: Sequence[str], *,
          work_kinds: Iterable[str], action_ops: Iterable[str] = ACTION_OPS
          ) -> None:
    """Zero-series for this platform's counters at boot — `increase()` over a
    series whose first sample is already 1 reads 0, so the counters the app
    increments later must exist first. Disabled platforms stay absent
    entirely (same rule as `nora_computer_up`'s labelled-gauge rationale).

    `work_kinds`/`action_ops` are the REAL label tuples (`bridge_store.
    WORK_KINDS`, `telemetry.ACTION_OPS`) — a placeholder `kind=""` primes a
    series no runtime increment will ever touch, which is the same
    not-primed hole this function exists to close.
    """
    if metrics is None:
        return
    for platform in platforms:
        for result in INSPECTION_RESULTS:
            metrics.inc("nora_bridge_inspections_total", 0,
                        platform=platform, result=result)
        for event in LIFECYCLE_EVENTS:
            metrics.inc("nora_bridge_queue_events_total", 0,
                        platform=platform, event=event)
        for kind in work_kinds:
            for outcome in WORK_OUTCOMES:
                metrics.inc("nora_bridge_work_attempts_total", 0,
                            platform=platform, kind=kind, outcome=outcome)
        for op in action_ops:
            for outcome in ACTION_OUTCOMES:
                metrics.inc("nora_bridge_actions_total", 0,
                            platform=platform, op=op, outcome=outcome)
