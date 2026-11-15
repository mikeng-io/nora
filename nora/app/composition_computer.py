"""The computer area's composition: stores, queue, scoped runners and the
`<computer>` context block, lifted whole out of `build_dependencies`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComputerWiring:
    browser_manager: Any
    computer: Any
    computer_admission: Any
    computer_context: Any
    computer_dm_scope_for: Any
    computer_events: Any
    computer_leases: Any
    computer_probe: Any
    computer_queue_runner: Any
    computer_scope_for: Any
    computer_sessions: Any
    computer_task_backend: Any


async def build_computer(
    *,
    allowlist: Any, background: Callable[[], Any], cfg: Any, clock: Any,
    compute_jobs: Any, computer_context: Callable[[], Any], engine: Any,
    metrics: Any, op_clock: Any,
) -> ComputerWiring:
    """stores → queue → scoped → context. The scoped runners win over the
    queue's values where both name one. `background`/`computer_context` are
    thunks resolved at use time — `build_dependencies` owns the forward
    declarations they close over."""
    wire = importlib.import_module("nora.computer").wire
    stores = wire.stores(cfg=cfg, engine=engine, metrics=metrics)
    queue = wire.queue()
    scoped = await wire.scoped(
        allowlist=allowlist,
        background=background,
        browser_tabs=queue.browser_tabs,
        cfg=cfg,
        clock=clock,
        compute_jobs=compute_jobs,
        computer=stores.computer,
        computer_admission=queue.computer_admission,
        computer_context=computer_context,
        computer_dm_scope_for=queue.computer_dm_scope_for,
        computer_events=stores.computer_events,
        computer_metrics=stores.computer_metrics,
        computer_probe=stores.computer_probe,
        computer_tasks=queue.computer_tasks,
        computer_token=stores.computer_token,
        engine=engine,
        metrics=metrics,
        op_clock=op_clock,
    )
    context = wire.context(
        allowlist=allowlist,
        browser_tabs=scoped.browser_tabs,
        cfg=cfg,
        computer=scoped.computer,
        computer_events=stores.computer_events,
        computer_scope_for=scoped.computer_scope_for,
        computer_sessions=stores.computer_sessions,
        computer_tasks=scoped.computer_tasks,
        metrics=metrics,
    )
    return ComputerWiring(
        browser_manager=scoped.browser_manager,
        computer=scoped.computer,
        computer_admission=scoped.computer_admission,
        computer_context=context.computer_context,
        computer_dm_scope_for=scoped.computer_dm_scope_for,
        computer_events=stores.computer_events,
        computer_leases=stores.computer_leases,
        computer_probe=scoped.computer_probe,
        computer_queue_runner=scoped.computer_queue_runner,
        computer_scope_for=scoped.computer_scope_for,
        computer_sessions=stores.computer_sessions,
        computer_task_backend=scoped.computer_task_backend,
    )
