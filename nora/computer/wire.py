import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

from nora.agent import factory, tool_choice
from nora.agent.browse import BrowseAgentRunner, BrowseSettings
from nora.agent.catalog.groups import COMPUTER_TOOLS
from nora.app.composition_helpers import _note_unit_of
from nora.app.computer_metrics import ComputerMetrics
from nora.app.computer_sweeper import ComputerSessionSweeper, SweepRules
from nora.app.startup import StartupError
from nora.computer.adapters.tools import build as _build
from nora.config import Config
from nora.domain.computer import ComputerToolSettings
from nora.domain.computer_context import ComputerContextCaps
from nora.domain.computer_events import ComputerEvent, ComputerEventStore
from nora.domain.computer_exports import ComputerExportStore
from nora.domain.computer_leases import ComputerLeases
from nora.domain.computer_sessions import ComputerSessionStore
from nora.domain.ports import SandboxRuntime
from nora.engagement.computer_context import ComputerContextReader
from nora.infra.computer.probe import ComputerdProbe
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.computer_exports import SqlComputerExportStore
from nora.infra.sql.computer_sessions import SqlComputerSessionStore

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps: Any) -> Any:

    return _build(deps)


@dataclass(frozen=True)
class StoresWiring:
    computer: Any
    computer_events: Any
    computer_leases: Any
    computer_metrics: Any
    computer_probe: Any
    computer_sessions: Any
    computer_token: Any


def stores(cfg: Any, engine: Any, metrics: Any)-> StoresWiring:

    # The computer audit log (DEC-243). Built UNCONDITIONALLY, like `tool_call_log_store`:
    # the computer tools write a row on every call, `unavailable` included, so a call made
    # while `[computer]` is off is audited too. Only the runtime is behind the flag.
    computer_events: ComputerEventStore = SqlComputerEventStore(engine)
    # Computer sessions, derived by code on every computer call, and the process's ONE lease
    # table: one instance shared by every turn and the sweeper, which is what makes an in-
    # process lock a lock (one Nora process; `ComputerLeases` says so).
    computer_sessions: ComputerSessionStore = SqlComputerSessionStore(engine)
    computer_leases = ComputerLeases()
    # The runtime: `ComputerdRuntime`, built ONLY when `[computer] enabled`
    # (see the settings below for the rest of `[computer]`). Built here,
    # before the assembler, because `<computer>` reads it on every turn.
    computer: SandboxRuntime | None = None
    computer_probe: ComputerdProbe | None = None
    if cfg.computer.enabled:
        from nora.infra.computer.client import ComputerdRuntime

        computer_token = _computer_token(cfg)
        # The computer as a metric (DEC-261): built ONLY here, so with `[computer] enabled =
        # false` there is no `nora_computer_up` series at all (not applicable, which is not
        # down) and no zero-valued calls series either. `prime()` creates them at zero, so a…
        computer_metrics = ComputerMetrics(metrics, audit_tools=COMPUTER_TOOLS)
        computer_metrics.prime()
        computer = ComputerdRuntime(
            base_url=cfg.computer.base_url,
            token=computer_token,
            timeout_seconds=cfg.computer.request_timeout_seconds,
            max_output_chars=cfg.computer.max_output_chars,
            observer=computer_metrics,
        )
        computer_probe = ComputerdProbe(
            base_url=cfg.computer.base_url,
            token=computer_token,
            observer=computer_metrics,
        )
    return StoresWiring(
        computer=computer,
        computer_events=computer_events,
        computer_leases=computer_leases,
        computer_metrics=computer_metrics,
        computer_probe=computer_probe,
        computer_sessions=computer_sessions,
        computer_token=computer_token,
    )


@dataclass(frozen=True)
class QueueWiring:
    browser_manager: Any
    browser_tabs: Any
    computer_admission: Any
    computer_dm_scope_for: Any
    computer_queue_runner: Any
    computer_scope_for: Any
    computer_task_backend: Any
    computer_tasks: Any


def queue() -> QueueWiring:
    # The scoped computer ([computer.scoped], design/browser-runtime): one microVM per
    # computer scope, resolved through the `nora-computer scope provision` registry — built
    # INSTEAD of the shared-VM client, so a call for a scope with no record answers…
    computer_admission: Any = None
    browser_manager: Any = None
    computer_queue_runner: Any = None
    computer_task_backend: Any = None
    computer_tasks: Any = None
    browser_tabs: Any = None
    #: The canonical scope resolvers ([computer.scoped]); `None` in the
    #: shared-VM shape, where nothing scoped may resolve.
    computer_scope_for: Any = None
    computer_dm_scope_for: Any = None
    return QueueWiring(
        browser_manager=browser_manager,
        browser_tabs=browser_tabs,
        computer_admission=computer_admission,
        computer_dm_scope_for=computer_dm_scope_for,
        computer_queue_runner=computer_queue_runner,
        computer_scope_for=computer_scope_for,
        computer_task_backend=computer_task_backend,
        computer_tasks=computer_tasks,
    )


@dataclass(frozen=True)
class ScopedWiring:
    browser_manager: Any
    browser_tabs: Any
    computer: Any
    computer_admission: Any
    computer_dm_scope_for: Any
    computer_probe: Any
    computer_queue_runner: Any
    computer_scope_for: Any
    computer_task_backend: Any
    computer_tasks: Any


async def scoped(
    allowlist: Any, background: Any, browser_tabs: Any, computer_context: Any,
    cfg: Any, clock: Any, compute_jobs: Any, computer: Any,
    computer_admission: Any, computer_dm_scope_for: Any, computer_events: Any,
    computer_metrics: Any, computer_tasks: Any, computer_token: Any, engine: Any,
    metrics: Any, op_clock: Any,
) -> ScopedWiring:
    browser_manager = computer_probe = computer_queue_runner = None
    computer_scope_for = computer_task_backend = None
    if cfg.computer.enabled and cfg.computer.scoped.enabled:
        rt = _scoped_computer_runtime(
            cfg=cfg, computer_metrics=computer_metrics,
            computer_token=computer_token, engine=engine,
        )
        computer = rt.computer
        computer_probe = rt.computer_probe
        computer_tasks = rt.computer_tasks
        browser_tabs = rt.browser_tabs
        queues = await _scoped_computer_queues(
            allowlist=allowlist, background=background, clock=clock,
            compute_jobs=compute_jobs, computer=rt.computer,
            computer_context=computer_context, computer_events=computer_events,
            computer_redis=rt.computer_redis, computer_segments=rt.computer_segments,
            computer_tasks=rt.computer_tasks, computer_token=computer_token,
            lifecycle=rt.lifecycle, metrics=metrics, op_clock=op_clock,
            scoped_cfg=cfg.computer.scoped, tabs=browser_tabs,
        )
        browser_manager = queues.browser_manager
        computer_admission = queues.computer_admission
        computer_queue_runner = queues.computer_queue_runner
        computer_task_backend = queues.computer_task_backend
        resolvers = _computer_scope_resolvers(allowlist)
        computer_dm_scope_for = resolvers.computer_dm_scope_for
        computer_scope_for = resolvers.computer_scope_for
    return ScopedWiring(
        browser_manager=browser_manager,
        browser_tabs=browser_tabs,
        computer=computer,
        computer_admission=computer_admission,
        computer_dm_scope_for=computer_dm_scope_for,
        computer_probe=computer_probe,
        computer_queue_runner=computer_queue_runner,
        computer_scope_for=computer_scope_for,
        computer_task_backend=computer_task_backend,
        computer_tasks=computer_tasks,
    )


@dataclass(frozen=True)
class ScopedRuntimeWiring:
    browser_tabs: Any
    computer: Any
    computer_probe: Any
    computer_redis: Any
    computer_segments: Any
    computer_tasks: Any
    lifecycle: Any


def _scoped_computer_runtime(
    cfg: Any, computer_metrics: Any, computer_token: Any, engine: Any,
) -> ScopedRuntimeWiring:

    """The scoped-computer daemon handle plus the stores and registries it drives."""
    scoped_cfg = cfg.computer.scoped
    if not scoped_cfg.registry:
        raise StartupError(
            "[computer.scoped] enabled but `registry` is empty: it must "
            "name the directory `nora-computer scope provision` writes."
        )
    computer_redis_url = cfg.credentials.get(scoped_cfg.redis_url_env, "")
    if not computer_redis_url:
        raise StartupError(
            f"[computer.scoped] enabled but ${scoped_cfg.redis_url_env} "
            "(the dedicated nq:computer:* streams) is not set."
        )
    from nora.app.computer_queue import SegmentRegistry
    from nora.domain.computer_vms import FileScopeEndpoints
    from nora.infra.computer.lifecycle import HostLifecycleClient
    from nora.infra.computer.scoped import ScopedComputerdRuntime
    from nora.infra.sql.browser_tabs import SqlBrowserTabStore
    from nora.infra.sql.computer_tasks import SqlComputerTaskStore

    return ScopedRuntimeWiring(
        browser_tabs=SqlBrowserTabStore(engine),
        computer=ScopedComputerdRuntime(
            endpoints=FileScopeEndpoints(scoped_cfg.registry),
            token=computer_token,
            timeout_seconds=cfg.computer.request_timeout_seconds,
            max_output_chars=cfg.computer.max_output_chars,
            observer=computer_metrics,
        ),
        computer_probe=None,
        computer_redis=aioredis.from_url(computer_redis_url),
        computer_segments=SegmentRegistry(
            pending_dir=Path(cfg.computer.state_dir) / "computer"
        ),
        computer_tasks=SqlComputerTaskStore(engine),
        lifecycle=(
            HostLifecycleClient(
                socket=scoped_cfg.lifecycle_socket, token=computer_token
            )
            if scoped_cfg.lifecycle_socket
            else None
        ),
    )


@dataclass(frozen=True)
class ScopedQueuesWiring:
    browser_manager: Any
    computer_admission: Any
    computer_queue_runner: Any
    computer_task_backend: Any


async def _scoped_computer_queues(
    allowlist, background, clock, compute_jobs, computer, computer_context, computer_events,
    computer_redis, computer_segments, computer_tasks, computer_token, lifecycle, metrics,
    op_clock, scoped_cfg, tabs,
) -> ScopedQueuesWiring:
    """The admission FIFO, task backend, queue runner and tab manager."""
    from nora.app.computer_queue import (
        ComputerAdmission,
        ComputerQueueRunner,
        ComputerTaskBackend,
        deliver_computer_task,
    )
    from nora.domain.computer import anything_running
    from nora.infra.computer.manager import ScopeBrowserManager

    _queue_audit, _browser_event = _computer_event_callbacks(
        background=background, clock=clock,
        computer_context=computer_context, computer_events=computer_events,
    )

    async def _deliver_computer_task(row: Any)-> None:

        await deliver_computer_task(
            row, jobs=compute_jobs, tasks=computer_tasks, clock=op_clock
        )

    async def _reconcile_scope(scope: str)-> str:

        """Is work REALLY running on the scope's VM? Read through"""
        state = await computer.state(scope=scope)
        verdict = anything_running(state)
        if verdict is None and lifecycle is not None:
            observed = await lifecycle.request("observe", scope, str(uuid.uuid4()))
            if observed.get("status") == "ok" and observed.get("state") == "stopped":
                return "quiet"  # host positively confirmed this owned VM is stopped
        return "busy" if verdict is True else "quiet" if verdict is False else "unknown"

    computer_admission = ComputerAdmission(
        tasks=computer_tasks,
        redis=computer_redis,
        clock=op_clock,
        executors=computer_segments,
        reconcile=_reconcile_scope,
        audit=_queue_audit,
        lifecycle=lifecycle,
    )
    return ScopedQueuesWiring(
        browser_manager=ScopeBrowserManager(
            runtime=computer,
            tabs=tabs,
            tasks=computer_tasks,
            segments=computer_admission,
            token=computer_token,
            clock=op_clock,
            idle_after=timedelta(seconds=scoped_cfg.browser_idle_seconds),
            on_event=_browser_event,
            lifecycle=lifecycle,
        ),
        computer_admission=computer_admission,
        computer_queue_runner=ComputerQueueRunner(
            tasks=computer_tasks,
            redis=computer_redis,
            clock=op_clock,
            metrics=metrics,
            on_terminal=_deliver_computer_task,
            reconcile=_reconcile_scope,
            executors=computer_segments,
            audit=_queue_audit,
            lifecycle=lifecycle,
        ),
        computer_task_backend=ComputerTaskBackend(tasks=computer_tasks),
    )


def _computer_event_callbacks(
    background, clock, computer_context, computer_events,
) -> Any:
    """The ComputerEvent writers the queue and tab manager publish through."""
    async def _queue_audit(task: Any, transition: str)-> None:

        """A queue lifecycle transition (granted/cancelled/interrupted/"""
        if computer_events is None:
            return
        origin = dict(task.origin or {})
        await computer_events.append(
            ComputerEvent(
                id=uuid.uuid4(),
                operation_id=task.id,
                phase="finished",
                created_at=clock(),
                started_at=task.started_at or task.created_at,
                finished_at=clock(),
                actor="sweeper",
                requester_person_id=str(origin.get("requester_person_id") or "") or None,
                platform=str(origin.get("platform") or ""),
                conversation_id=str(origin.get("conversation_id") or ""),
                conversation_type=str(origin.get("conversation_type") or "group"),
                tag_id=str(origin.get("tag_id") or ""),
                scope=task.scope,
                tool="computer_queue",
                path="direct",
                trace_id="",
                span_id="",
                status=transition,
                result={"task": str(task.id), "kind": task.kind, "transition": transition},
            )
        )

    def _browser_event(scope: str, transition: str, subject: Any)-> Any:

        """A browser lifecycle transition (tab expired, browser stopped)"""
        ctx = computer_context()
        if ctx is not None:
            ctx.invalidate(scope)
        if computer_events is None:
            return

        async def _write() -> None:
            await computer_events.append(
                ComputerEvent(
                    id=uuid.uuid4(),
                    operation_id=uuid.uuid4(),
                    phase="finished",
                    created_at=clock(),
                    started_at=clock(),
                    finished_at=clock(),
                    actor="sweeper",
                    requester_person_id=None,
                    platform="",
                    conversation_id="",
                    conversation_type="",
                    tag_id="",
                    scope=scope,
                    tool="computer_browser",
                    path="direct",
                    trace_id="",
                    span_id="",
                    status=transition,
                    result={"transition": transition, "subject": str(subject or "")},
                )
            )

        with contextlib.suppress(Exception):
            return background().spawn(_write())

    return _queue_audit, _browser_event


@dataclass(frozen=True)
class ScopeResolversWiring:
    computer_dm_scope_for: Any
    computer_scope_for: Any


def _computer_scope_resolvers(allowlist: Any)-> ScopeResolversWiring:

    """Room -> the canonical VM scope it is pinned to (DM pair or unit)."""
    from nora.domain.computer import computer_dm_scope, computer_unit_scope, dm_scope_id

    def computer_dm_scope_for(platform: str, conversation_id: str)-> str | None:

        convo = allowlist.conversation(platform, conversation_id)
        if convo is None or convo.persons is None or len(convo.persons) != 1:
            return None
        return dm_scope_id(next(iter(convo.persons)), convo.unit)

    def computer_scope_for(
        platform: str, conversation_id: str, conversation_type: str,
    ) -> str:
        if conversation_type == "group":
            unit = _note_unit_of(allowlist)(platform, conversation_id)
            return computer_unit_scope(unit) if unit else ""
        scope_id = computer_dm_scope_for(platform, conversation_id)
        return computer_dm_scope(scope_id) if scope_id else ""

    return ScopeResolversWiring(
        computer_dm_scope_for=computer_dm_scope_for,
        computer_scope_for=computer_scope_for,
    )



@dataclass(frozen=True)
class ContextWiring:
    computer_context: Any


def context(
    allowlist: Any, browser_tabs: Any, cfg: Any, computer: Any,
    computer_events: Any, computer_scope_for: Any, computer_sessions: Any,
    computer_tasks: Any, metrics: Any,
) -> ContextWiring:
    # `<computer>`: the one reader the tag
    # path and the fire path share. `None` while `[computer]` is off, so no
    # room renders the block.
    computer_context: ComputerContextReader | None = None
    if computer is not None:
        computer_context = ComputerContextReader(
            sessions=computer_sessions,
            events=computer_events,
            runtime=computer,
            caps=ComputerContextCaps(
                **cfg.computer.context.model_dump(),
                idle_after=timedelta(minutes=cfg.computer.sessions.idle_after_minutes),
                lapsed_visible=timedelta(hours=cfg.computer.sessions.lapsed_visible_hours),
            ),
            clock=lambda: datetime.now(UTC),
            metrics=metrics,
            unit_for=_note_unit_of(allowlist),
            # The scoped runtime's canonical resolver wins over `unit_for`
            # when wired: a DM room's block reads ITS scope, a group's its
            # unit — the same resolution the tools and the queue share.
            scope_for=computer_scope_for,
            # The scoped runtime's queue/tab state in the same block: wired
            # only in scoped mode, so the shared-VM block is unchanged.
            tasks=computer_tasks,
            tabs=browser_tabs,
            browser_idle_after=(
                timedelta(seconds=cfg.computer.scoped.browser_idle_seconds)
                if cfg.computer.scoped.enabled
                else None
            ),
        )
    return ContextWiring(computer_context=computer_context)


@dataclass(frozen=True)
class BrowseWiring:
    browser: Any


def browse(
    browser_manager: Any, cfg: Any, computer: Any, model_client_for: Any,
    route_model: Any,
) -> BrowseWiring:

    # `computer_browse` (ADR 0022): a sub-agent in the room's own browser, with a model
    # source of its own (one model per task, the tag path's reason above). `None` unless
    # `[computer]` AND `[computer.browse]` are on, so the registry withholds the tool.
    browser: BrowseAgentRunner | None = None
    if computer is not None and cfg.computer.browse.enabled:
        browse_cfg = cfg.computer.browse
        browser = BrowseAgentRunner(
            model_source=factory.ModelSource(
                client=model_client_for("browse"),
                spec=factory.ModelSpec(
                    model=route_model("browse", browse_cfg.model),
                    max_tokens=browse_cfg.max_tokens,
                    temperature=browse_cfg.temperature,
                    tool_choice=tool_choice.AFTER_SPEAKING,
                ),
            ),
            retry=factory.build_retry_policy(timeout_seconds=browse_cfg.timeout_seconds),
            base_url=cfg.computer.base_url,
            token=_computer_token(cfg),
            # Task-owned tabs ([computer.scoped]): `acquire` binds the task's
            # own target on the scope's VM; `base_url` is only the shared-VM
            # fallback used when no manager is wired.
            manager=browser_manager,
            settings=BrowseSettings(
                max_steps=browse_cfg.max_steps,
                timeout_seconds=browse_cfg.timeout_seconds,
                read_chars=browse_cfg.read_chars,
                turns=browse_cfg.turns,
                output_tokens=browse_cfg.output_tokens,
                total_tokens=browse_cfg.total_tokens,
            ),
        )
    return BrowseWiring(browser=browser)


@dataclass(frozen=True)
class SettingsWiring:
    computer_settings: Any


def settings(cfg: Any)-> SettingsWiring:

    # Nora's computer (DEC-243): `ComputerdRuntime`, built ONLY when `[computer] enabled`.
    # Off leaves it `None` and every computer tool answers `unavailable` (R-5).
    # `ComputerToolSettings` is the conversion point from config to a domain value…
    computer_settings = ComputerToolSettings(
        run_timeout_seconds=cfg.computer.run_timeout_seconds,
        max_run_timeout_seconds=cfg.computer.max_run_timeout_seconds,
        max_output_chars=cfg.computer.max_output_chars,
        max_read_chars=cfg.computer.max_read_chars,
        export_max_bytes=cfg.computer.export_max_bytes,
        import_max_bytes=cfg.computer.import_max_bytes,
        limits=cfg.computer.limits.as_pairs(),
        idle_after_minutes=cfg.computer.sessions.idle_after_minutes,
        lapsed_visible_hours=cfg.computer.sessions.lapsed_visible_hours,
    )
    return SettingsWiring(computer_settings=computer_settings)


@dataclass(frozen=True)
class ExportsWiring:
    computer_exports: Any


def exports(engine: Any)-> ExportsWiring:

    # `computer_export`: its record of what left the computer, built unconditionally like
    # the audit store. The same store is the portal's Artifacts read (ADR 0023), and a link
    # is the portal's `<[documents] public_url>/artifacts/<artifact_id>`: without a…
    computer_exports: ComputerExportStore = SqlComputerExportStore(engine)
    return ExportsWiring(computer_exports=computer_exports)


@dataclass(frozen=True)
class DispatchWiring:
    pass


def dispatch(
    allowlist: Any, audit: Any, compute_backends: Any, computer_queue_runner: Any,
    computer_scope_for: Any, computer_task_backend: Any, memory: Any, metrics: Any,
    persons: Any, registry: Any, rosters: Any, tool_registry: Any,
) -> DispatchWiring:
    if computer_queue_runner is not None:
        # The queue's executor for durable `native`/`browse`/`capture` rows: each is re-entered
        # through the registry surface built for its ORIGIN room (`nora.app.computer_dispatch`)
        # — fresh policy admission, never a replayed closure.
        from nora.app.computer_dispatch import ComputerToolDispatch

        _computer_dispatch = ComputerToolDispatch(
            registry=tool_registry,
            store=audit,
            persons=persons,
            honcho=memory,
            metrics=metrics,
            rosters=rosters,
            self_ids=registry.self_aliases(),
            # Re-admission is decided at EXECUTION time: current policy and
            # the live scope resolver, never the queue-time row's word.
            policy=allowlist,
            scope_for=computer_scope_for,
        )
        for _kind in ("native", "browse", "capture"):
            computer_queue_runner.set_dispatch(_kind, _computer_dispatch)
    if computer_task_backend is not None:
        # The scoped computer's deferred-delivery half ([computer.scoped]):
        # a `computer_tasks` row that closed while its room was not waiting
        # is delivered through the same outbox every other kind uses.
        compute_backends["computer"] = computer_task_backend
    return DispatchWiring()


@dataclass(frozen=True)
class SweeperWiring:
    computer_sweeper: Any


def sweeper(
    cfg, computer, computer_events, computer_leases, computer_sessions, metrics,
) -> SweeperWiring:
    computer_sweeper = (
        ComputerSessionSweeper(
            sessions=computer_sessions,
            events=computer_events,
            runtime=computer,
            leases=computer_leases,
            rules=SweepRules(
                idle_expiry=timedelta(hours=cfg.computer.sessions.idle_expiry_hours),
                max_running=timedelta(days=cfg.computer.sessions.max_running_days),
                idle_after=timedelta(minutes=cfg.computer.sessions.idle_after_minutes),
            ),
            clock=lambda: datetime.now(UTC),
            metrics=metrics,
        )
        if computer is not None
        else None
    )
    return SweeperWiring(computer_sweeper=computer_sweeper)


def _computer_token(cfg: Config)-> str:

    """computerd's bearer token, checked at BOOT when `[computer] enabled`:"""
    token = cfg.credentials.get(cfg.computer.token_env, "")
    if not token.strip():
        raise StartupError(
            f"[computer] enabled = true but {cfg.computer.token_env} is unset or "
            "empty: it is computerd's bearer token (the value lives in .env, "
            "DEC-8). Starting without it would fail every computer call; "
            "refusing to start"
        )
    return token
