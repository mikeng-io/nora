"""`ScopeBrowserManager` — task-owned tabs on the scope's one Chrome.

One scope → one VM → one Chrome (computerd starts it on demand). Each BROWSER
task owns the tabs it bound (`browser_tasks` rows keyed
`(scope, instance, target_id)`); a continuation reattaches to ITS tab, a
second task gets its own, and nothing adopts the first page or closes a
sibling. Ownership is checked at every attach against the resolved VM
`instance`: a recreated VM makes every old row `expired` and its handles
dead, so a stale handle answers `expired`, never a substitute tab.

The population rule: at most `MAX_PAGE_TARGETS` page targets live per scope
(popups counted). A page target with no open row is an orphan — bound to the
task that is executing when it is found (`via="popup"`), or closed when no
task owns it. The cap refuses a NEW tab when four live; it does not evict an
active owner.

Lifecycle: authorized activity touches `last_activity_at` (page timers do
not — only manager calls move it); `sweep` expires tabs idle past
`idle_after`, closes their targets, and — when no open tab remains — asks
computerd to stop Chrome/Xvfb and confirms the exit through `state`.
"""

from __future__ import annotations

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

    async def _targets(self, base_url: str, scope: str) -> list[dict[str, Any]]:
        async with connect_browser_cdp(
            base_url=base_url, token=self._token, scope=scope,
        ) as cdp:
            got = await cdp.call("Target.getTargets")
            return [t for t in got.get("targetInfos", []) if t.get("type") == "page"]

    async def _reconcile(self, binding: ScopeBinding, scope: str,
                         live: set[str], now: datetime,
                         owner: UUID | None) -> None:
        """Match live page targets with open rows: an unbound page is an
        orphan (assigned to `owner`, the executing task, as a popup; closed
        when no task owns it — never exposed unattributed); a row whose
        target is gone is `closed`. Over-cap popups are refused and closed."""
        base = binding.endpoint.base_url
        try:
            targets = await self._targets(base, scope)
        except BrowserError:
            return
        rows = await self._open_rows(scope, binding.instance)
        live.update(str(t.get("targetId")) for t in targets)
        bound = {r.target_id: r for r in rows}
        for row in rows:
            if row.target_id not in live:
                with contextlib.suppress(Exception):
                    await self._tabs.set_status(row.id, status=CLOSED, now=now)
        orphans = [t for t in targets if str(t.get("targetId")) not in bound]
        room = max(0, MAX_PAGE_TARGETS - len(bound) - len(
            [t for t in orphans if owner is None]))
        async with connect_browser_cdp(
            base_url=base, token=self._token, scope=scope,
        ) as cdp:
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

    @contextlib.asynccontextmanager
    async def acquire(self, scope: str, task_id: UUID,
                    target_id: str | None = None) -> AsyncIterator[BoundTab]:
        """Attach the task's tab (its earlier `target_id`, or a fresh page).
        `target_id` naming another task's row or a dead one is
        `TabUnavailable("expired")`; a fresh page over the cap is
        `TabUnavailable("cap")`."""
        binding = await self._binding(scope)
        now = self._clock()
        await self._expire_other_instances(scope, binding.instance)
        rows = await self._open_rows(scope, binding.instance)
        mine = [r for r in rows if r.task_id == task_id and r.via == "task"]
        if target_id is not None:
            row = next((r for r in mine if r.target_id == target_id), None)
            if row is not None:
                wanted = target_id
            elif any(r.target_id == target_id for r in rows):
                raise TabUnavailable("denied")  # another task's tab
            else:
                raise TabUnavailable("expired")
        elif mine:
            wanted = mine[-1].target_id
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
                live: set[str] = set()
                await self._reconcile(binding, scope, live, now, owner=task_id)
                yield BoundTab(page=page, tab=tab, binding=binding)
                with contextlib.suppress(Exception):
                    snap_url = str(await page.evaluate("location.href") or "")
                    snap_title = str(await page.evaluate("document.title") or "")
                    await self._tabs.touch(tab.id, now=self._clock(),
                                           url=snap_url, title=snap_title)
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
        The image itself is returned, never logged."""
        async with self.acquire(scope, task_id) as bound:
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

    async def sweep(self, *, now: datetime | None = None) -> int:
        """Expire tabs idle past `idle_after`; close their targets; stop the
        browser when nothing open remains. Returns expiries."""
        now = now or self._clock()
        idle_before = now - self._idle_after
        try:
            expired = await self._tabs.expire_idle(
                idle_before=idle_before, now=now, limit=50)
        except Exception:
            log.exception("browser tab idle sweep failed")
            return 0
        stopped: set[str] = set()
        for tab in expired:
            if tab.scope in stopped:
                continue
            stopped.add(tab.scope)
            binding = await self._runtime.binding(tab.scope)
            if binding is None or binding.instance != tab.instance:
                continue  # dead VM: rows already expire on next acquire
            async with connect_browser_cdp(
                base_url=binding.endpoint.base_url, token=self._token,
                scope=tab.scope,
            ) as cdp:
                for row in [t for t in expired if t.scope == tab.scope]:
                    with contextlib.suppress(BrowserError):
                        await cdp.call("Target.closeTarget",
                                       {"targetId": row.target_id})
            try:
                left = await self._tabs.open_for_scope(tab.scope,
                                                       instance=binding.instance)
            except Exception:
                log.exception("browser tab open-row read failed")
                continue
            if not left:
                await self.browser_stop(tab.scope)
        return len(expired)
