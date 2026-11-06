"""`ScopeBrowserManager` — task-owned tabs on the scope's one Chrome.

One scope → one VM → one Chrome (computerd starts it on demand). Each BROWSER
task owns the tabs it bound (`browser_tasks` rows keyed
`(scope, instance, target_id)`); a continuation reattaches to ITS tab, a
second task gets its own, and nothing adopts the first page or closes a
sibling. Ownership is checked at every attach against the resolved VM
`instance`: a recreated VM makes every old row `expired` and its handles
dead, so a stale handle answers `expired`, never a substitute tab.

Handles are NEW or EXISTING, never fuzzy: an attach that names no task opens
a tab; one that names a task MUST find that task's row (`must_exist`) — a
foreign, invented, expired or stale-instance handle is refused, and
`capture` can never conjure a tab.

The population rule: at most `MAX_PAGE_TARGETS` page targets live per scope
(popups counted). A page target with no open row is an orphan — bound to the
task executing when it is found (`via="popup"`), or closed when no task
owns it. The cap refuses a NEW tab when four live; it does not evict an
active owner.

One CDP websocket: computerd admits a single browser connection per scope,
so the attach's connection is the one every `Target.getTargets`,
`Target.setDiscoverTargets` watch, `Target.closeTarget` and target event
runs on — reconciliation happens on the admitted connection at attach, on
target events while a segment runs, and once more after the segment hands
the page back (a popup the objective spawned is adopted or closed before
the next segment sees the browser).

Lifecycle: authorized activity touches `last_activity_at` (page timers do
not — only manager calls move it); `sweep` closes the targets of idle rows
from INSIDE the scope's elected owner — a `maintenance` segment claimed
through the same `try_claim_head` every execution segment uses, so
cleanup never runs beside a live owner and no successor is granted while
it runs — with the guest effect confirmed BEFORE the row expires, a
failed close left open for the next pass — and when no open tab remains,
asks computerd to stop Chrome/Xvfb and confirms the exit through `state`.
A scope mid-attach is skipped, never raced.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import aiohttp

from nora.domain.browser_tasks import (
    CLOSED,
    EXPIRED,
    MAX_PAGE_TARGETS,
    OPEN,
    BrowserTab,
    BrowserTabStore,
)
from nora.domain.computer import SandboxStatus
from nora.domain.computer_tasks import (
    QUEUED,
    ComputerTask,
    ComputerTaskStore,
    current_segment,
)
from nora.infra.computer.browser import (
    BrowserError,
    BrowserPage,
    connect_browser_cdp,
    open_browser_tab,
)
from nora.infra.computer.scoped import ScopeBinding, ScopedComputerdRuntime

log = logging.getLogger("nora.computer.manager")

__all__ = ["BoundTab", "CapturedImage", "ScopeBrowserManager"]

#: Sweep cadence: how often idle tabs are reaped (driven by the caller's loop).
SWEEP_SECONDS = 30.0

#: The deadline on a maintenance segment: the owner row a cleanup claim
#: holds while it reconciles targets, closes idle tabs and stops the
#: browser. Past it the watchdog cancels the executor — a hung sweep
#: never wedges the scope's queue.
MAINTENANCE_LEASE = timedelta(seconds=300)


class TabUnavailable(Exception):  # noqa: N818 - the name IS the outcome word
    """The requested tab could not be bound: no VM, a stale/expired handle,
    or the four-target cap. `reason` is the outcome word the caller maps."""
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BoundTab:
    """A live attach: the page, its binding row and the verified instance."""

    page: BrowserPage
    tab: BrowserTab
    binding: ScopeBinding


@dataclass(frozen=True, slots=True)
class CapturedImage:
    """A saved capture's provenance: bytes on the workspace, the digest,
    and the typed identity of WHERE it was taken — scope, VM instance
    (boot), owning task and CDP target — so an export/audit row cannot
    present an image divorced from the state that produced it."""

    path: str
    media_type: str
    size: int
    sha256: str
    scope: str
    instance: str
    task_id: UUID
    tab_id: UUID
    target_id: str
    url: str
    title: str
    partial: bool
    captured_at: datetime


class ScopeBrowserManager:
    def __init__(
        self, *, runtime: ScopedComputerdRuntime, tabs: BrowserTabStore,
        token: str, clock: Callable[[], datetime], idle_after: timedelta,
        observer: Any = None,
        # The execution-owner machinery ([computer.scoped]): `tasks` is the
        # durable `computer_tasks` store and `segments` the
        # `ComputerAdmission` that runs a claimed row through the shared
        # executor registry. Both wired, cleanup claims the SAME elected
        # scope owner every segment holds — a `running` row blocks the
        # claim, and cleanup's own running row blocks every other claim.
        tasks: ComputerTaskStore | None = None,
        segments: Any = None,
        # Lifecycle events to the existing audit facilities:
        # `(scope, transition, subject)` — `tab_expired`, `browser_stopped`,
        # `popup_closed`. None keeps cleanup silent.
        on_event: Callable[[str, str, Any], Any] | None = None,
    ) -> None:
        self._runtime = runtime
        self._tabs = tabs
        self._token = token
        self._clock = clock
        self._idle_after = idle_after
        self._observer = observer
        self._tasks = tasks
        self._segments = segments
        self._on_event = on_event
        #: The task currently holding a live attach per scope — the owner any
        #: unbound page target found during its attach is assigned to.
        self._active: dict[str, UUID] = {}
        #: One lock per scope serializes attach/reconcile against the sweep's
        #: close path, so an idle pass can never kill a tab mid-segment.
        self._locks: dict[str, asyncio.Lock] = {}
        #: The serialized reconcile drain per scope (event-driven target
        #: changes funnel through it) and a flag meaning "another event
        #: arrived while one ran".
        self._drains: dict[str, asyncio.Task[None]] = {}
        self._drain_again: set[str] = set()
        self._events: set[asyncio.Task[Any]] = set()

    def _lock(self, scope: str) -> asyncio.Lock:
        return self._locks.setdefault(scope, asyncio.Lock())

    async def aclose(self) -> None:
        drains = list(self._drains.values())
        for task in drains:
            task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        self._drains.clear()
        self._drain_again.clear()
        await asyncio.gather(*tuple(self._events))

    async def tasks_for_segment(self, scope: str, segment_id: UUID) -> set[UUID]:
        binding = await self._runtime.binding(scope)
        if binding is None:
            raise TabUnavailable("the VM identity is unavailable")
        return {row.task_id for row in await self._open_rows(scope, binding.instance)
                if str(row.detail.get("segment_id") or "") == str(segment_id)}

    def _note(self, scope: str, transition: str, subject: Any = None) -> None:
        """Lifecycle instrumentation on the existing audit hook — never
        blocks cleanup: a listener failure is logged, not raised."""
        if self._on_event is None:
            return
        try:
            event = self._on_event(scope, transition, subject)
            if isinstance(event, asyncio.Task):
                self._events.add(event)
                event.add_done_callback(self._events.discard)
        except Exception:
            log.exception("browser lifecycle listener failed")

    async def _binding(self, scope: str) -> ScopeBinding:
        binding = await self._runtime.binding(scope)
        if binding is None:
            raise TabUnavailable("unavailable")
        return binding

    async def _expire_other_instances(self, scope: str, instance: str) -> None:
        with contextlib.suppress(Exception):
            await self._tabs.expire_instance(scope=scope, instance=instance,
                                             now=self._clock())

    async def _open_rows(self, scope: str, instance: str) -> list[BrowserTab]:
        try:
            return await self._tabs.open_for_scope(scope, instance=instance)
        except Exception:
            log.exception("browser tab store unreadable")
            raise TabUnavailable("unavailable") from None

    async def _reconcile(self, scope: str, binding: ScopeBinding,
                         cdp: Any, now: datetime, owner: UUID | None) -> None:
        """Match live page targets with open rows ON THE ADMITTED
        CONNECTION: an unbound page is an orphan. It is bound to the task
        that owns its ACTUAL `openerId` (`via="popup"`) — never to the
        `owner` task passed by the caller, which is only the task whose
        attach triggered the pass — and an orphan whose opener is unknown
        or whose bind did not land is closed, not exposed unattributed; a
        row whose target is gone is `closed`. `getTargets` itself is
        authoritative: a failed read raises `TabUnavailable`, it does not
        silently retire or adopt anything."""
        try:
            got = await cdp.call("Target.getTargets")
        except BrowserError as exc:
            raise TabUnavailable("unavailable") from exc
        targets = [t for t in got.get("targetInfos", []) if t.get("type") == "page"]
        live = {str(t.get("targetId")) for t in targets}
        rows = await self._open_rows(scope, binding.instance)
        bound = {r.target_id: r for r in rows}
        for row in rows:
            if row.target_id not in live:
                with contextlib.suppress(Exception):
                    await self._tabs.set_status(row.id, status=CLOSED, now=now,
                                                reason="the target is gone")
        orphans = [t for t in targets if str(t.get("targetId")) not in bound]
        count = len(bound)
        for orphan in orphans:
            target_id = str(orphan.get("targetId"))
            opener = str(orphan.get("openerId") or "")
            parent = bound.get(opener) if opener else None
            if parent is not None and count < MAX_PAGE_TARGETS:
                try:
                    await self._tabs.bind(BrowserTab(
                        id=uuid.uuid4(), scope=scope, task_id=parent.task_id,
                        instance=binding.instance, target_id=target_id,
                        status=OPEN, created_at=now, last_activity_at=now,
                        via="popup",
                        detail={
                            "segment_id": str(current_segment.get())
                            if current_segment.get() is not None else "",
                            "opener": opener,
                        },
                        last_url=str(orphan.get("url") or ""),
                        last_title=str(orphan.get("title") or "")))
                    count += 1
                    continue
                except Exception:  # noqa: BLE001 - a failed bind refuses the page
                    # A bind that did not land leaves the page unattributed:
                    # fall through to the refused close, never leave an
                    # unowned target live.
                    log.warning("browser popup bind failed; the page is refused")
            # No bound opener, a failed bind, or the cap full: the page is
            # refused. A refused popup counts against nothing — it never
            # lived.
            with contextlib.suppress(BrowserError):
                await cdp.call("Target.closeTarget", {"targetId": target_id})

    def _schedule_reconcile(self, scope: str, binding: ScopeBinding,
                            cdp: Any, owner: UUID) -> asyncio.Task[None]:
        """Funnel target-change work through the scope's drain: reconciles
        never overlap, and an event arriving mid-pass re-runs once."""
        running = self._drains.get(scope)
        if running is not None and not running.done():
            self._drain_again.add(scope)
            return running

        async def _drain() -> None:
            while True:
                self._drain_again.discard(scope)
                try:
                    await self._reconcile(scope, binding, cdp,
                                          now=self._clock(), owner=owner)
                except TabUnavailable:
                    # A failed read touched nothing: fail closed and let the
                    # next event (or the post-attach reconcile) retry.
                    log.warning("browser reconcile unreadable (scope %s…)",
                                scope[:8])
                if scope not in self._drain_again:
                    return

        drain = asyncio.ensure_future(_drain())
        self._drains[scope] = drain
        drain.add_done_callback(lambda t: self._drains.pop(scope, None))
        return drain

    @contextlib.asynccontextmanager
    async def acquire(
        self, scope: str, task_id: UUID, target_id: str | None = None, *,
        must_exist: bool = False,
    ) -> AsyncIterator[BoundTab]:
        """Attach the task's tab (its earlier `target_id`, or a fresh page).

        `must_exist` is the EXISTING-handle rule: the task must already own a
        `via="task"` row on the LIVE instance — a foreign, invented, expired
        or stale-instance handle is `TabUnavailable("expired")`/`("denied")`,
        never a substitute tab. `target_id` naming another task's row or a
        dead one is refused the same way; a fresh page over the cap is
        `TabUnavailable("cap")`."""
        async with self._lock(scope):
            binding = await self._binding(scope)
            now = self._clock()
            await self._expire_other_instances(scope, binding.instance)
            rows = await self._open_rows(scope, binding.instance)
            mine = [r for r in rows if r.task_id == task_id and r.via == "task"]
            if target_id is not None:
                if any(r.target_id == target_id and r.task_id == task_id
                       for r in mine):
                    wanted: str | None = target_id
                elif any(r.target_id == target_id for r in rows):
                    raise TabUnavailable("denied")  # another task's tab
                else:
                    raise TabUnavailable("expired")
            elif mine:
                wanted = mine[-1].target_id
            elif must_exist:
                raise TabUnavailable("expired")
            else:
                if len(rows) >= MAX_PAGE_TARGETS:
                    raise TabUnavailable("cap")
                wanted = None
            self._active[scope] = task_id
            try:
                async with open_browser_tab(
                    base_url=binding.endpoint.base_url, token=self._token,
                    scope=scope, target_id=wanted, instance=binding.instance,
                ) as page:
                    now = self._clock()
                    if wanted is None:
                        tab = await self._tabs.bind(BrowserTab(
                            id=uuid.uuid4(), scope=scope, task_id=task_id,
                            instance=binding.instance, target_id=page.target_id,
                            status=OPEN, created_at=now, last_activity_at=now,
                            detail={"segment_id": str(current_segment.get())}
                            if current_segment.get() is not None else {}))
                    else:
                        tab = next(r for r in rows if r.target_id == page.target_id)
                        await self._tabs.touch(tab.id, now=now, segment_id=current_segment.get())
                        tab = (await self._tabs.get(tab.id)) or tab
                    cdp = page.cdp
                    # Reconcile on THIS connection, then watch target events
                    # for the segment's length: a popup is adopted or closed
                    # while it is born, not on a later poll.
                    await self._reconcile(scope, binding, cdp, now, owner=task_id)
                    with contextlib.suppress(BrowserError):
                        await cdp.call("Target.setDiscoverTargets",
                                       {"discover": True})

                    def _on_event(method: str, params: dict[str, Any]) -> None:
                        if method.startswith("Target."):
                            self._schedule_reconcile(scope, binding, cdp, task_id)

                    with contextlib.suppress(AttributeError):
                        cdp.subscribe(_on_event)
                    try:
                        yield BoundTab(page=page, tab=tab, binding=binding)
                    finally:
                        with contextlib.suppress(AttributeError):
                            cdp.unsubscribe(_on_event)
                        with contextlib.suppress(Exception):
                            snap_url = str(await page.evaluate("location.href") or "")
                            snap_title = str(await page.evaluate("document.title") or "")
                            await self._tabs.touch(tab.id, now=self._clock(),
                                                   url=snap_url, title=snap_title)
                        # Post-objective reconcile: a popup the segment
                        # spawned is adopted to this task or closed before
                        # the next segment sees the browser.
                        drain = self._schedule_reconcile(
                            scope, binding, cdp, task_id)
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(asyncio.shield(drain), 10.0)
            except BrowserError as exc:
                # A refused or dead connection may be a replaced VM: the
                # cached binding goes BEFORE any outcome is answered — an
                # `expired` answer included — so no stale authority stays.
                self._runtime.invalidate(scope)
                # A bound row whose target vanished between the read and the
                # attach is `expired`, not a wrong tab.
                if wanted is not None:
                    with contextlib.suppress(Exception):
                        gone = [r for r in rows if r.target_id == wanted]
                        for r in gone:
                            await self._tabs.set_status(
                                r.id, status=EXPIRED, now=self._clock(),
                                reason="the target is gone")
                    raise TabUnavailable("expired") from None
                raise TabUnavailable("unavailable") from exc
            finally:
                self._active.pop(scope, None)

    async def capture(self, scope: str, task_id: UUID, *, full_page: bool,
                      directory: str = "captures") -> CapturedImage:
        """A direct image of the task's tab (no decision loop), saved into
        the bound workspace through `put`; the row's provenance rides along.
        The image itself is returned, never logged. `must_exist`: capture is
        always an EXISTING handle — a missing, foreign or stale task is
        `expired`; it never opens a tab."""
        async with self.acquire(scope, task_id, must_exist=True) as bound:
            now = self._clock()
            data, media_type, partial = await bound.page.capture_image(
                full_page=full_page)
            if not data:
                # An empty frame is not a picture: fail closed rather than
                # stamp a digest over nothing.
                raise TabUnavailable("unavailable")
            url = str(await bound.page.evaluate("location.href") or "")
            title = str(await bound.page.evaluate("document.title") or "")
            digest = hashlib.sha256(data).hexdigest()
            ext = "png" if media_type.endswith("png") else "jpg"
            path = f"{directory}/{now.strftime('%Y%m%dT%H%M%S')}-{digest[:8]}.{ext}"
            stored = await self._runtime.put(scope=scope, path=path, data=data)
            if stored.status is not SandboxStatus.OK:
                raise TabUnavailable(
                    "unavailable" if stored.status is SandboxStatus.UNAVAILABLE
                    else "denied")
            return CapturedImage(
                path=path, media_type=media_type, size=len(data), sha256=digest,
                scope=scope, instance=bound.binding.instance,
                task_id=task_id, tab_id=bound.tab.id,
                target_id=bound.page.target_id, url=url, title=title,
                partial=partial, captured_at=now)

    async def _owns(self, scope: str) -> bool:
        """Is the caller already INSIDE a segment owning this scope? Then
        the grant is held and guest effects are made directly — a re-claim
        would see its own `running` row and refuse itself."""
        if self._tasks is None:
            return False
        segment = current_segment.get()
        if segment is None:
            return False
        try:
            owner = await self._tasks.get(segment)
        except Exception:  # noqa: BLE001 - an unreadable store is never proof of ownership
            return False
        return (owner is not None and owner.scope == scope
                and owner.status == "running")

    async def _claim_maintenance(self, scope: str) -> ComputerTask | None:
        """Claim the scope's EXECUTION owner for a cleanup pass — the same
        `computer_tasks` row and `try_claim_head` election every segment
        goes through (advisory lock, oldest-queued, no-running). None when
        an owner is mid-segment, the store cannot prove quiet, or real
        work precedes: cleanup never jumps the FIFO, never races a
        segment, and never leaves a `maintenance` row parked in the
        queue — a failed claim is cancelled in the same call.

        A claim that lands runs through `run_segment`, so the shared
        registry sees the executor and the terminal row closes on the
        same rules as every other segment."""
        if self._tasks is None or self._segments is None:
            return None
        try:
            open_rows = await self._tasks.open_for_scope(scope, limit=20)
        except Exception:  # noqa: BLE001 - an unreadable owner store is never a quiet scope
            return None
        if open_rows:
            # A `running` row is an owner mid-segment; a `queued` row is
            # work ahead of ours — either way the claim below could only
            # add a never-head row, so the pass is skipped WITHOUT writing
            # one. (A running row past the page is impossible: it claimed
            # before every queued sibling, so it sorts first.)
            return None
        now = self._clock()
        row = ComputerTask(
            id=uuid.uuid4(), scope=scope, kind="maintenance", status=QUEUED,
            payload={"maintenance": "browser_lifecycle"}, origin={},
            created_at=now, updated_at=now,
            deadline_at=now + MAINTENANCE_LEASE)
        try:
            await self._tasks.enqueue(row)
            claimed = await self._tasks.try_claim_head(row.id, now=self._clock())
        except Exception:  # noqa: BLE001 - the owner store is the boundary; it must answer
            with contextlib.suppress(Exception):
                await self._tasks.request_cancel(row.id, now=self._clock())
            return None
        if claimed is None:
            # A competitor's row landed between the read and the claim:
            # close ours at once — a maintenance row never waits its turn.
            with contextlib.suppress(Exception):
                await self._tasks.request_cancel(row.id, now=self._clock())
            return None
        return claimed

    async def browser_stop(self, scope: str) -> bool:
        """Ask computerd to stop the scope's browser and confirm, through
        `state`, that it is gone. False when the VM could not answer or the
        browser is still up.

        The stop runs INSIDE the scope's elected execution owner, not just
        under the manager lock: it claims a `maintenance` segment through
        the same `try_claim_head` every segment uses, so a `running` row —
        a native or worker segment, which may be about to attach — makes
        the claim fail and the browser is never stopped under it. Called
        inside a segment that already owns the scope (the sweep's own),
        the guest call is made directly."""
        if await self._owns(scope):
            return await self._browser_stop_guest(scope)
        claimed = await self._claim_maintenance(scope)
        if claimed is not None:
            reply = await self._segments.run_segment(
                claimed, self._stop_owned(scope))
            return bool(reply.get("stopped"))
        if self._segments is not None:
            return False  # owned path wired and the scope was not claimable
        if self._tasks is not None:
            try:
                busy = any(
                    t.status == "running"
                    for t in await self._tasks.open_for_scope(scope, limit=20))
            except Exception:  # noqa: BLE001 - an unreadable store is never a quiet owner
                return False
            if busy:
                return False
        return await self._browser_stop_guest(scope)

    async def _stop_owned(self, scope: str) -> dict[str, Any]:
        stopped = await self._browser_stop_guest(scope)
        return {"status": "ok" if stopped else "nothing_found",
                "stopped": stopped}

    async def _browser_stop_guest(self, scope: str) -> bool:
        """The guest half of `browser_stop`: POST the stop, then confirm
        through `state` that the browser is gone. Caller holds the scope's
        owner."""
        binding = await self._runtime.binding(scope)
        if binding is None:
            return False
        url = binding.endpoint.base_url + "/v1/browser_stop"
        try:
            async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30)) as s, s.post(url, headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Computerd-Instance": binding.instance},
                    json={"scope": scope}) as r:
                if r.status != 200:
                    # A refused stop (a mismatched instance included) makes
                    # the binding suspect: re-verify before the next pass.
                    self._runtime.invalidate(scope)
                    return False
        except Exception:  # noqa: BLE001 - an unanswering VM reads as "not stopped"
            return False
        state = await self._runtime.state(scope=scope)
        running = bool((state.browser or {}).get("running")) \
            if state.status is SandboxStatus.OK else None
        stopped = running is False
        if stopped:
            for row in await self._open_rows(scope, binding.instance):
                await self._tabs.set_status(
                    row.id, status="expired", now=self._clock(),
                    reason="the browser stopped")
                self._note(scope, "tab_expired", row.id)
            self._note(scope, "browser_stopped", scope)
        return stopped

    async def _close_confirmed(self, cdp: Any, target_id: str) -> bool:
        """Retire a target only once the guest CONFIRMS it is gone:
        `Target.closeTarget`'s own `success`, or a follow-up `getTargets`
        that no longer lists it. False leaves the row open — a retired row
        on a live target is exactly the stale-handle hole this guards."""
        try:
            got = await cdp.call("Target.closeTarget", {"targetId": target_id})
        except BrowserError as exc:
            return "gone" in str(exc).lower()
        if isinstance(got, dict) and got.get("success") is True:
            return True
        try:
            listed = await cdp.call("Target.getTargets")
        except BrowserError:
            return False
        infos = listed.get("targetInfos", []) if isinstance(listed, dict) else []
        return str(target_id) not in {str(t.get("targetId")) for t in infos}

    async def close_task(self, scope: str, task_id: UUID, *,
                         reason: str = "the task was cancelled") -> int:
        """Retire ONLY `task_id`'s tabs: close its open targets on the
        scope's CDP connection (a sibling task's tabs are never touched)
        and stamp each confirmed row `closed` with `reason` and the time.
        Runs inside the scope's elected owner — a `maintenance` claim
        unless the caller already holds the grant — so a busy scope
        answers `-1` (could not enter safely: retry), never a pretend
        retirement. `0` is the honest "nothing was open"."""
        try:
            task_id = UUID(str(task_id))
        except (ValueError, TypeError):
            return -1
        if await self._owns(scope):
            reply = await self._close_task_owned(scope, task_id, reason)
        else:
            claimed = await self._claim_maintenance(scope)
            if claimed is None:
                return -1  # a live owner holds the scope, or no owner wired
            reply = await self._segments.run_segment(
                claimed, self._close_task_owned(scope, task_id, reason))
        try:
            return int(reply.get("closed", -1))
        except (TypeError, ValueError, AttributeError):
            return -1

    async def _close_task_owned(self, scope: str, task_id: UUID,
                                reason: str) -> dict[str, Any]:
        """The owned half of `close_task`, caller holding the scope's
        grant. A VM or store that cannot answer is `-1`, not `0`."""
        async with self._lock(scope):
            binding = await self._runtime.binding(scope)
            if binding is None:
                return {"status": "unavailable", "closed": -1}
            try:
                rows = await self._tabs.open_for_scope(scope,
                                                       instance=binding.instance)
            except Exception:
                log.exception("browser tab close-task read failed")
                return {"status": "unavailable", "closed": -1}
            mine = [r for r in rows
                    if r.task_id == task_id and r.status == OPEN]
            if not mine:
                return {"status": "ok", "closed": 0}
            closed = 0
            try:
                async with connect_browser_cdp(
                    base_url=binding.endpoint.base_url,
                    token=self._token, scope=scope,
                    instance=binding.instance,
                ) as cdp:
                    for row in mine:
                        if not await self._close_confirmed(cdp, row.target_id):
                            continue  # unconfirmed: left open, reported below
                        with contextlib.suppress(Exception):
                            await self._tabs.set_status(
                                row.id, status=CLOSED, now=self._clock(),
                                reason=reason)
                            closed += 1
            except BrowserError:
                return {"status": "unavailable", "closed": -1}
            return {"status": "ok" if closed == len(mine) else "unavailable",
                    "closed": closed if closed == len(mine) else -1}

    async def _browser_running(self, scope: str) -> bool | None:
        """Whether the scope's browser verifiably runs, from `state`: True,
        False, or None when the VM could not say — unknown is never "down"."""
        try:
            state = await self._runtime.state(scope=scope)
        except Exception:  # noqa: BLE001 - an unreachable VM is "unknown", never "down"
            return None
        if (state.status is not SandboxStatus.OK
                or not isinstance(state.browser, dict)):
            return None
        return bool(state.browser.get("running"))

    async def _sweep_scope(self, scope: str, now: datetime) -> int:
        """One scope's reconcile and idle close, run INSIDE the scope's
        elected execution owner when one is wired.

        Rows on a DEAD instance expire outright (their targets went with the
        VM). Rows on the live instance are closed on the scope's own CDP
        connection and expired only once the guest effect is confirmed —
        a refused close leaves the row open for the next pass, never
        silently leaked, and a busy scope is skipped, never raced.

        The pass claims a `maintenance` segment through the same
        `try_claim_head` election a native or browse segment holds: a
        `running` row blocks the pass outright (the read-before-write that
        once let a stop land under a mid-segment owner is gone — the claim
        IS the check), a `queued` row makes the pass wait for real work,
        and while the pass holds its row no successor can be granted. A
        scope with nothing open and no live browser is left untouched —
        an empty tick never writes a maintenance row."""
        binding = await self._runtime.binding(scope)
        if binding is None:
            return 0  # no verified VM: nothing safe to do this pass
        try:
            rows = await self._tabs.for_scope(scope, limit=200)
        except Exception:
            log.exception("browser tab sweep read failed")
            return 0
        open_rows = [r for r in rows if r.status == OPEN]
        browser_up = await self._browser_running(scope)
        if not open_rows and browser_up is not True:
            return 0  # nothing bound and no live browser to stop
        if self._segments is None or self._tasks is None:
            # The un-owned half (no execution owner wired): the in-process
            # lock is the only boundary the scope has.
            lock = self._lock(scope)
            if lock.locked():
                return 0  # mid-attach: next pass retries
            async with lock:
                return await self._sweep_owned(scope, binding, open_rows,
                                               browser_up, now)
        claimed = await self._claim_maintenance(scope)
        if claimed is None:
            return 0  # an owner holds the scope, or real work precedes
        reply = await self._segments.run_segment(
            claimed, self._sweep_segment(scope, now))
        try:
            return int(reply.get("expired") or 0)
        except (TypeError, ValueError):
            return 0

    async def _sweep_segment(self, scope: str, now: datetime) -> dict[str, Any]:
        """The maintenance segment's work: the full pass, re-read under
        the grant so what was true at claim time is re-verified inside the
        owner."""
        async with self._lock(scope):
            binding = await self._runtime.binding(scope)
            if binding is None:
                return {"status": "unavailable", "expired": 0}
            try:
                rows = await self._tabs.for_scope(scope, limit=200)
            except Exception:
                log.exception("browser tab sweep read failed")
                return {"status": "unavailable", "expired": 0}
            open_rows = [r for r in rows if r.status == OPEN]
            browser_up = await self._browser_running(scope)
            expired = await self._sweep_owned(scope, binding, open_rows,
                                              browser_up, now)
            return {"status": "ok", "expired": expired}

    async def _sweep_owned(
        self, scope: str, binding: ScopeBinding, open_rows: list[BrowserTab],
        browser_up: bool | None, now: datetime,
    ) -> int:
        """The pass itself, caller holding the scope's owner (the
        maintenance grant, or — un-owned mode — just the manager lock)."""
        idle_before = now - self._idle_after
        dead = [r for r in open_rows if r.instance != binding.instance]
        live_rows = [r for r in open_rows if r.instance == binding.instance]
        candidates = [r for r in live_rows
                      if r.last_activity_at < idle_before
                      and r.task_id != self._active.get(scope)]
        expired = 0
        now2 = self._clock()
        for row in dead:
            with contextlib.suppress(Exception):
                await self._tabs.set_status(row.id, status="expired", now=now2,
                                            reason="the VM instance changed")
                expired += 1
                self._note(scope, "tab_expired", row.id)
        if candidates or browser_up is True:
            try:
                async with connect_browser_cdp(
                    base_url=binding.endpoint.base_url,
                    token=self._token, scope=scope,
                    instance=binding.instance,
                ) as cdp:
                    try:
                        await self._reconcile(scope, binding, cdp, now=now2,
                                              owner=None)
                    except TabUnavailable:
                        # An unreadable target list is not proof of quiet:
                        # leave every row for the next pass.
                        return expired
                    for row in candidates:
                        # Re-read idle inside the lock: an attach that
                        # touched it while we waited is spared.
                        fresh = await self._tabs.get(row.id)
                        if (fresh is None or fresh.status != OPEN
                                or fresh.last_activity_at >= idle_before):
                            continue
                        if not await self._close_confirmed(cdp, row.target_id):
                            continue  # guest effect unconfirmed: retry next pass
                        with contextlib.suppress(Exception):
                            await self._tabs.set_status(
                                row.id, status="expired", now=self._clock(),
                                reason="idle")
                            expired += 1
                            self._note(scope, "tab_expired", row.id)
            except BrowserError:
                # No connection: every close stays pending, retried; the
                # binding itself is suspect — re-verify before next pass.
                self._runtime.invalidate(scope)
        try:
            left = await self._tabs.open_for_scope(scope, instance=binding.instance)
        except Exception:
            log.exception("browser tab open-row read failed")
            return expired
        # Stop the browser only once nothing open remains AND the
        # machine still says it runs — or the last row just expired
        # this pass. A failed stop stays retryable: the registry keeps
        # this scope in `sweep`'s discovery while its browser runs.
        # `browser_stop` dispatches: inside this maintenance segment the
        # grant is already held (`_owns`), in un-owned mode its busy
        # check is the boundary.
        if not left and (browser_up is True or open_rows):
            await self.browser_stop(scope)
        return expired

    async def sweep(self, *, now: datetime | None = None) -> int:
        """Expire tabs idle past `idle_after`, scopes one at a time;
        reconcile every scope whose browser still runs, and stop it when
        nothing open remains. Returns confirmed expiries.

        Discovery is the tab store's open scopes UNION the registry's
        provisioned ones: a scope whose browser is still up with all tabs
        already closed or expired is still visited — the stop that failed
        is retried, the guest process is never leaked by silence."""
        now = now or self._clock()
        scopes: set[str] = set()
        try:
            scopes.update(await self._tabs.open_scopes(limit=200))
        except Exception:
            log.exception("browser tab scope sweep read failed")
            return 0
        registered = getattr(self._runtime, "registered", None)
        if registered is not None:
            with contextlib.suppress(Exception):
                scopes.update(await registered())
        total = 0
        for scope in sorted(scopes):
            try:
                total += await self._sweep_scope(scope, now)
            except Exception:
                log.exception("browser tab sweep failed (scope %s…)", scope[:8])
        return total
