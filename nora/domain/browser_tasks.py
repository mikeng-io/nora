"""Browser task bindings: which task owns which tab on which VM instance.

`browser_tasks` (migration `0147`) records an OWNERSHIP fact, not a page:
task `id` owns CDP `target_id` inside scope `scope` on VM `instance`. It is
what lets a continuation find its own tab after a turn ends, what stops one
task closing another's page, and what makes a recreated VM visible: a row's
`instance` that no longer matches the resolved endpoint's instance is
`expired` — the tab is gone with the old VM, and the handle must answer so,
never silently attach to a replacement.

Popups a task opens are rows of the same task (`via="popup"`): they count
against the scope's four-target cap and are closed with it.

`last_url`/`last_title` are OBSERVED page text — untrusted, never used for
actions, refreshed on activity. Status: `open` while the tab may live,
`closed` when the task released or the tab was seen gone, `expired` when the
idle sweep or a new VM instance retired it. Nothing is deleted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from nora.domain.computer_events import ensure_utc_event

__all__ = [
    "CLOSED", "EXPIRED", "OPEN", "TAB_STATUSES", "BrowserTab",
    "BrowserTabStore", "normalise_tab",
]

OPEN, CLOSED, EXPIRED = "open", "closed", "expired"
TAB_STATUSES: frozenset[str] = frozenset({OPEN, CLOSED, EXPIRED})
_SCOPE = re.compile(r"[0-9a-f]+")

#: The cap on live page targets in one scope's browser, popups included.
MAX_PAGE_TARGETS = 4


@dataclass(frozen=True, slots=True)
class BrowserTab:
    id: UUID
    scope: str
    task_id: UUID
    instance: str
    target_id: str
    status: str
    created_at: datetime
    last_activity_at: datetime
    via: str = "task"  # "task" or "popup"
    last_url: str = ""
    last_title: str = ""
    detail: Any = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in TAB_STATUSES:
            raise ValueError(f"unknown browser tab status {self.status!r}")
        if not _SCOPE.fullmatch(self.scope or ""):
            raise ValueError(f"browser tab scope is not a hex key: {self.scope!r}")


def normalise_tab(tab: BrowserTab) -> BrowserTab:
    return replace(
        tab,
        created_at=ensure_utc_event(tab.created_at, field="created_at"),
        last_activity_at=ensure_utc_event(tab.last_activity_at, field="last_activity_at"),
        detail=dict(tab.detail),
    )


@runtime_checkable
class BrowserTabStore(Protocol):
    """`browser_tasks`. Implementations:
    `nora.infra.sql.browser_tabs.SqlBrowserTabStore` and
    `nora.testing.FakeBrowserTabStore`. MAY raise on storage failure."""

    async def bind(self, tab: BrowserTab) -> BrowserTab: ...

    async def get(self, tab_id: UUID) -> BrowserTab | None: ...

    async def for_scope(self, scope: str, *, limit: int) -> list[BrowserTab]:
        """The scope's rows, newest first, any status — the `<computer>`
        block's read (an expired tab is reported expired, not hidden)."""
        ...

    async def open_for_scope(self, scope: str, *, instance: str) -> list[BrowserTab]:
        """Open rows on this VM instance — the population the four-target cap
        counts and the reconcile pass compares with live targets."""
        ...

    async def touch(self, tab_id: UUID, *, now: datetime,
                    url: str = "", title: str = "") -> None:
        """Activity on a bound tab moves `last_activity_at` (authorized work
        refreshes; page timers do not — only rows a caller touches move)."""
        ...

    async def set_status(self, tab_id: UUID, *, status: str, now: datetime) -> None: ...

    async def open_scopes(self, *, limit: int) -> list[str]:
        """The scopes with any `open` row: the sweep's breadth read, from the
        table — never a key scan."""
        ...

    async def expire_instance(self, *, scope: str, instance: str,
                              now: datetime) -> int:
        """Every open row of `scope` NOT on `instance` → `expired`: the VM
        was recreated, so its old tabs are gone. Returns the count."""
        ...
