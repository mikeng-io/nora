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
on their scope's own serialized path — the guest effect confirmed BEFORE
the row expires, a failed close left open for the next pass — and when no
open tab remains, asks computerd to stop Chrome/Xvfb and confirms the exit
through `state`. A scope mid-attach is skipped, never raced.
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
    MAX_PAGE_TARGETS,
    OPEN,
    BrowserTab,
    BrowserTabStore,
)
from nora.domain.computer import SandboxStatus
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
    """A saved capture's provenance: bytes on the workspace and the digest."""

    path: str
    media_type: str
    size: int
    sha256: str
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
    ) -> None:
        self._runtime = runtime
        self._tabs = tabs
        self._token = token
        self._clock = clock
        self._idle_after = idle_after
        self._observer = observer
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

    def _lock(self, scope: str) -> asyncio.Lock:
        return self._locks.setdefault(scope, asyncio.Lock())

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
        CONNECTION: an unbound page is an orphan (assigned to `owner`, the
        executing task, as a popup; closed when no task owns it or the cap
        is full — never exposed unattributed); a row whose target is gone
        is `closed`."""
        try:
            got = await cdp.call("Target.getTargets")
        except BrowserError:
            return
        targets = [t for t in got.get("targetInfos", []) if t.get("type") == "page"]
        live = {str(t.get("targetId")) for t in targets}
        rows = await self._open_rows(scope, binding.instance)
        bound = {r.target_id: r for r in rows}
        for row in rows:
            if row.target_id not in live:
                with contextlib.suppress(Exception):
                    await self._tabs.set_status(row.id, status=CLOSED, now=now)
        orphans = [t for t in targets if str(t.get("targetId")) not in bound]
        room = max(0, MAX_PAGE_TARGETS - len(bound) - len(
            [t for t in orphans if owner is None]))
        for orphan in orphans:
            target_id = str(orphan.get("targetId"))
            if owner is not None and room > 0:
                room -= 1
                with contextlib.suppress(Exception):
                    await self._tabs.bind(BrowserTab(
                        id=uuid.uuid4(), scope=scope, task_id=owner,
                        instance=binding.instance, target_id=target_id,
                        status=OPEN, created_at=now, last_activity_at=now,
                        via="popup",
                        last_url=str(orphan.get("url") or ""),
                        last_title=str(orphan.get("title") or "")))
                continue
            # No owner, or the cap is full: the page is refused. A refused
            # popup counts against nothing — it never lived.
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
                await self._reconcile(scope, binding, cdp,
                                      now=self._clock(), owner=owner)
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
                    scope=scope, target_id=wanted,
                ) as page:
                    now = self._clock()
                    if wanted is None:
                        tab = await self._tabs.bind(BrowserTab(
                            id=uuid.uuid4(), scope=scope, task_id=task_id,
                            instance=binding.instance, target_id=page.target_id,
                            status=OPEN, created_at=now, last_activity_at=now))
                    else:
                        tab = next(r for r in rows if r.target_id == page.target_id)
                        await self._tabs.touch(tab.id, now=now)
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
                # A bound row whose target vanished between the read and the
                # attach is `expired`, not a wrong tab.
                if wanted is not None:
                    with contextlib.suppress(Exception):
                        gone = [r for r in rows if r.target_id == wanted]
                        for r in gone:
                            await self._tabs.set_status(r.id, status=CLOSED,
                                                        now=self._clock())
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
                task_id=task_id, tab_id=bound.tab.id,
                target_id=bound.page.target_id, url=url, title=title,
                partial=partial, captured_at=now)

    async def browser_stop(self, scope: str) -> bool:
        """Ask computerd to stop the scope's browser and confirm, through
        `state`, that it is gone. False when the VM could not answer or the
        browser is still up."""
        binding = await self._runtime.binding(scope)
        if binding is None:
            return False
        url = binding.endpoint.base_url + "/v1/browser_stop"
        try:
            async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30)) as s, s.post(url, headers={
                    "Authorization": f"Bearer {self._token}"},
                    json={"scope": scope}) as r:
                if r.status != 200:
                    return False
        except Exception:  # noqa: BLE001 - an unanswering VM reads as "not stopped"
            return False
        state = await self._runtime.state(scope=scope)
        running = bool((state.browser or {}).get("running")) \
            if state.status is SandboxStatus.OK else None
        return running is False

    async def _sweep_scope(self, scope: str, now: datetime) -> int:
        """One scope's idle close, serialized against its own attaches.

        Rows on a DEAD instance expire outright (their targets went with the
        VM). Rows on the live instance are closed on the scope's own CDP
        connection and expired only once the guest effect is confirmed —
        a refused close leaves the row open for the next pass, never
        silently leaked, and a busy scope is skipped, never raced."""
        lock = self._lock(scope)
        if lock.locked():
            return 0  # mid-attach: next pass retries
        async with lock:
            binding = await self._runtime.binding(scope)
            idle_before = now - self._idle_after
            try:
                rows = await self._tabs.for_scope(scope, limit=200)
            except Exception:
                log.exception("browser tab sweep read failed")
                return 0
            open_rows = [r for r in rows if r.status == OPEN]
            if not open_rows:
                return 0
            if binding is None:
                return 0  # no verified VM: nothing safe to do this pass
            dead = [r for r in open_rows if r.instance != binding.instance]
            live_rows = [r for r in open_rows if r.instance == binding.instance]
            candidates = [r for r in live_rows
                          if r.last_activity_at < idle_before
                          and r.task_id != self._active.get(scope)]
            if not dead and not candidates:
                return 0
            expired = 0
            now2 = self._clock()
            for row in dead:
                with contextlib.suppress(Exception):
                    await self._tabs.set_status(row.id, status="expired", now=now2)
                    expired += 1
            if candidates:
                try:
                    async with connect_browser_cdp(
                        base_url=binding.endpoint.base_url,
                        token=self._token, scope=scope,
                    ) as cdp:
                        for row in candidates:
                            # Re-read idle inside the lock: an attach that
                            # touched it while we waited is spared.
                            fresh = await self._tabs.get(row.id)
                            if (fresh is None or fresh.status != OPEN
                                    or fresh.last_activity_at >= idle_before):
                                continue
                            try:
                                await cdp.call("Target.closeTarget",
                                               {"targetId": row.target_id})
                            except BrowserError as exc:
                                if "gone" not in str(exc).lower():
                                    continue  # guest effect unconfirmed: retry next pass
                            with contextlib.suppress(Exception):
                                await self._tabs.set_status(
                                    row.id, status="expired", now=self._clock())
                                expired += 1
                except BrowserError:
                    pass  # no connection: every close stays pending, retried
            try:
                left = await self._tabs.open_for_scope(scope, instance=binding.instance)
            except Exception:
                log.exception("browser tab open-row read failed")
                return expired
            if not left:
                await self.browser_stop(scope)
            return expired

    async def sweep(self, *, now: datetime | None = None) -> int:
        """Expire tabs idle past `idle_after`, scopes one at a time; stop the
        browser when nothing open remains. Returns confirmed expiries."""
        now = now or self._clock()
        try:
            scopes = await self._tabs.open_scopes(limit=200)
        except Exception:
            log.exception("browser tab scope sweep read failed")
            return 0
        total = 0
        for scope in scopes:
            try:
                total += await self._sweep_scope(scope, now)
            except Exception:
                log.exception("browser tab sweep failed (scope %s…)", scope[:8])
        return total
