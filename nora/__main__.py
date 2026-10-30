"""Entry point: python -m nora

Thin by design (spec C.1, D.1): `build_app` loads config, builds the object
graph through `nora.app.composition.build_dependencies`, and constructs an
`App`. All fatal, ordered startup validation (`StartupError`) now lives in
`build_dependencies` itself — this module only wraps the one step that comes
before it, loading `cfg` from disk.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import signal
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import config
from .agent.telemetry import setup_agent_tracing
from .app import adoption, cli, runtime, wake
from .app.composition import Dependencies, StartupError, build_dependencies
from .app.revisions import RevisionRecorder
from .config import ConfigError
from .obs.errors import setup_errors
from .obs.health import build_health_app, serve
from .obs.logging import heartbeat, setup_logging
from .obs.tracing import build_sampler
from .web.api import register_web_routes
from .web.otp import register_otp_routes
from .web.voice import register_voice

PRUNE_INTERVAL_SECONDS = 300.0
DEBOUNCE_SWEEP_INTERVAL_SECONDS = 300.0
DEBOUNCE_SWEEP_TTL_SECONDS = 3600.0
# How often computerd is asked whether it answers (DEC-261). 30s, the scrape
# interval: a faster probe is a series Prometheus never sees, and ComputerDown
# waits minutes anyway. One authenticated read of a scope that does not exist.
COMPUTER_PROBE_INTERVAL_SECONDS = 30.0
# The participation loop ticks fast enough that a closed window is evaluated
# promptly (its settle window is ~10s; a 2s tick bounds the lag), and sweeps
# rooms idle for a day — the trigger's cursors are per-room, so the TTL is
# about a room going quiet for good, not about a conversation gap.
PARTICIPATION_TICK_SECONDS = 2.0
PARTICIPATION_SWEEP_TTL_SECONDS = 86400.0
ADOPTION_START_DELAY_SECONDS = adoption.DEFAULT_START_DELAY_SECONDS

log = logging.getLogger("nora.__main__")

__all__ = ["App", "StartupError", "build_app", "build_parser", "main", "run_until_stopped"]

#: The signals that stop the process gracefully. `docker stop` (and so a compose
#: recreate) sends SIGTERM; a terminal's Ctrl-C sends SIGINT.
STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGINT)


async def build_app(config_path: Path) -> App:
    try:
        cfg = config.load(config_path)
    except ConfigError as exc:
        raise StartupError(str(exc)) from exc

    setup_logging(
        level=cfg.obs.logging.level,
        format=cfg.obs.logging.format,
        secrets=cfg.redaction_values(),
    )
    # BEFORE `setup_tracing` and before anything that can fail: the whole point
    # is to capture a startup crash, and a reporter wired after the code that
    # crashes reports nothing. It is wired AFTER `setup_logging` because it
    # rides that logger's records.
    setup_errors(
        enabled=cfg.obs.errors.enabled,
        dsn_env=cfg.obs.errors.dsn_env, credentials=cfg.credentials,
        environment=cfg.obs.errors.environment,
        # No release is passed, so Sentry groups by environment alone and "is this
        # new since the last deploy" is not answerable from an issue. Wiring the
        # running version through (`nora.infra.version`, which reads the
        # build-time `nora/version.json`; absent in a dev checkout) would make it so.
    )
    if cfg.obs.tracing.enabled:
        try:
            setup_agent_tracing(
                sampler=build_sampler(), service_name="nora",
                exporter=cfg.obs.tracing.exporter,
                endpoint=cfg.obs.tracing.endpoint,
                headers_env=cfg.obs.tracing.headers_env, credentials=cfg.credentials,
            )
        except Exception:
            # Tracing is never load-bearing (nora.obs.tracing.span's own rule): a bad
            # endpoint or an unreachable collector must never take the app down. The
            # guard lives here because nora.obs may not import nora.agent.
            log.warning("tracing setup failed; continuing without tracing", exc_info=True)

    deps = await build_dependencies(cfg)
    return App(cfg=cfg, deps=deps, config_path=config_path)


class App:
    def __init__(self, *, cfg, deps: Dependencies, config_path: Path | None = None) -> None:
        self.cfg = cfg
        self.deps = deps
        self.config_path = config_path
        self._health_runner = None

    # Convenience accessors: tests and the health app read these directly
    # rather than reaching into `deps`.
    @property
    def adapter(self):
        return self.deps.adapter

    @property
    def account(self) -> str:
        return self.deps.account

    @property
    def doctrine(self):
        return self.deps.doctrine

    @property
    def store(self):
        return self.deps.audit

    @property
    def honcho(self):
        return self.deps.memory

    @property
    def llm_state(self):
        return self.deps.llm_state

    async def run(self) -> None:
        deps = self.deps
        # Shared with register_web_routes below so /health's uptime and
        # /app/v1/status's uptime never disagree about when this process
        # started.
        started_at = time.time()
        health_app = build_health_app(
            adapter=deps.adapter, adapters=deps.registry.adapters,
            store=deps.audit, honcho=deps.memory,
            doctrine_store=deps.doctrine_store, metrics=deps.metrics,
            started_at=started_at, llm_state=deps.llm_state,
            identity_file=self.cfg.identity_file, policy_file=self.cfg.policy_file,
            index_state=deps.index_state, config_reload=deps.config_reload.status,
        )
        register_web_routes(
            health_app, store=deps.audit, honcho=deps.memory, reminders=deps.reminders,
            persons=deps.persons, ai_peer=self.cfg.honcho.ai_peer, started_at=started_at,
            games_client=deps.games_client,
            presence=deps.presence, presence_circles=deps.presence_circles,
            mobility=deps.mobility,
            skills_dir=deps.skills_dir, events=deps.events,
            doctrine_store=deps.doctrine_store,
            workspaces_of=deps.workspaces_of,
            profile=deps.profile_portal,
            profile_scopes_of=deps.profile_scopes_of,
            profile_cited=deps.profile_cited,
            documents=deps.documents, rooms_of=deps.rooms_of,
            room_event_shown=deps.room_event_shown, blobs=deps.blobs,
            room_label=deps.room_label, artifacts=deps.artifacts,
        )
        # One-time codes (`nora.app.otp`): mounted only when `[otp] enabled`,
        # and then only behind the bearer token composition refused to start
        # without.
        if deps.otp is not None:
            register_otp_routes(health_app, otp=deps.otp, api_token=deps.otp_api_token)
        # The voice channel's text entry point: the wake service POSTs transcripts
        # here, and they enter through the dispatcher exactly like a typed Discord
        # message (same adapter callback, ingest and reactive path). `on_voice_event`
        # is the audio-controller's callback channel, present only when the consumer
        # was built (hub + music manager).
        register_voice(
            health_app, ingest=deps.dispatcher.on_message,
            # A guild's speech is recorded in the room the operator marked
            # `voice = true`. Passed as the resolved id, or None to refuse;
            # `nora.web` never sees a `Policy`.
            resolve_voice_room=deps.resolve_voice_room,
            on_voice_event=(
                deps.voice_event_consumer.consume
                if deps.voice_event_consumer is not None else None
            ),
        )
        self._health_runner = await serve(health_app, self.cfg.obs.metrics.port)

        deps.background.spawn(heartbeat(deps.metrics))
        # What this process loaded: its deploy, its three config files, and
        # every doctrine and Dreamer load from now on, so `nora snapshot --at
        # T` can say what was in force. Spawned, never awaited: a slow or
        # failing database costs the rows, not the start.
        recorder = RevisionRecorder(
            store=deps.revisions, doctrine=deps.doctrine_store,
            dreamer=deps.dreamer_instructions, metrics=deps.metrics,
            config_files=tuple(
                (name, path) for name, path in (
                    ("config.toml", self.config_path),
                    ("policy.toml", self.cfg.policy_file),
                    ("identity.toml", self.cfg.identity_file),
                ) if path is not None
            ),
        )
        deps.background.spawn(self._record_revisions(
            recorder, started_at=datetime.fromtimestamp(started_at, tz=UTC),
        ))
        deps.background.spawn(self._update_last_event_age())
        # Lazy adoption, spawned exactly like the prune sweep and for the same
        # reason: it writes to the record store, and the ingest path drops a
        # message it cannot audit. Nothing awaits it; a slow or failing
        # backfill must not delay the adapters coming up.
        deps.background.spawn(self._adopt_pending())
        # Conditional, unlike its neighbours: `store.prune_enabled = false` means
        # the record is kept in full, and the sweep is not merely widened but
        # never started — `prune_all` scans the whole `messages` table per tick,
        # so a huge window would pay for that forever to delete nothing.
        if self.cfg.store.prune_enabled:
            deps.background.spawn(runtime.prune_loop(
                deps.audit, deps.metrics, interval_seconds=PRUNE_INTERVAL_SECONDS,
            ))
        else:
            log.info("retention sweep disabled (store.prune_enabled = false)")
        if deps.graph_batches is not None:
            deps.background.spawn(deps.graph_batches.run())
        if deps.comprehend_runner is not None:
            deps.background.spawn(deps.comprehend_runner.run())
        if deps.extraction is not None:
            deps.background.spawn(deps.extraction.run())
        # Unconditional: every store write projects into the memory index,
        # so there is always a queue a failed projection may have filled.
        deps.background.spawn(runtime.memory_drain_loop(
            deps.audit, deps.metrics,
            interval_seconds=self.cfg.memory.index.refresh_seconds,
            # `[memory.index] embed`; None while off.
            embed=deps.memory_embed,
        ))
        # The Dreamer's clock, the SAME conditional shape as the retention
        # sweep above: `[dreamer] enabled = false` (the shipped default) means
        # composition built no cursor store, so there is nothing this loop could
        # tick even if spawned.
        #
        # `deps.language_store is not None` is the SECOND condition, not a
        # restatement of the first: it remembers where each adaptor got to
        # (`consolidation_watermark`, reached through `nora.infra.sql.language`).
        # So this reads "the clock is wanted, and the cursor exists".
        if self.cfg.dreamer.enabled and deps.language_store is not None:
            # BEFORE the loop starts, and awaited rather than spawned. An
            # adaptor with a new name has no watermark row, `watermark_for`
            # returns 0 for an unknown pair, and it would re-read every room
            # from the beginning — `batch_limit` rows at a time, a model call
            # per batch, re-deriving evidence that already exists. Safe (the
            # natural key makes the append idempotent), slow, expensive, and it
            # looks exactly like a bug. Racing the loop against its own seeding
            # would leave which one won up to the scheduler.
            await runtime.seed_watermarks(
                deps.language_store, deps.dream_adaptors,
                # The schedules, so an adaptor can ask to seed at the RECORD
                # HEAD when there is nothing to inherit — see
                # `AdaptorSchedule.seed_at_head`. Without them the fallback is
                # unreachable and a scope the source never covered stays at 0.
                schedules=deps.dream_schedules,
                keys=lambda a: (
                    deps.unit_scopes() if a.key_kind == "unit"
                    else deps.language_scopes()
                ),
            )
            deps.background.spawn(runtime.dream_loop(
                deps.audit, deps.language_store,
                deps.metrics,
                scopes=deps.language_scopes,
                # The shared media join: every scope-keyed dream adaptor reads one
                # batch, so joining it once here covers all of them and none has to
                # hold a media store.
                media_join=deps.media_join,
                tick_seconds=self.cfg.dreamer.tick_seconds,
                safety_lag=self.cfg.dreamer.safety_lag,
                quiet_seconds=self.cfg.dreamer.quiet_seconds,
                max_age_seconds=self.cfg.dreamer.max_age_seconds,
                batch_limit=self.cfg.dreamer.batch_limit,
                instructions=deps.dreamer_instructions,
                adaptors=deps.dream_adaptors,
                runtime=deps.dreamer_runtime,
                units=deps.unit_scopes,
                # Per-adaptor clocks: a cron-shaped adaptor's own interval and batch
                # live here, not in `[dreamer]`'s shared numbers.
                schedules=deps.dream_schedules,
                # Where a watermark-free scheduled adaptor's last fire is kept. In
                # Postgres rather than in `dream_loop`'s frame because the frame is
                # rebuilt on every restart, which would turn a daily schedule into a
                # per-deploy one.
                schedule_clock=deps.dream_schedule_clock,
                # The ONE wall clock around a whole (adaptor, key) unit: it covers
                # everything the loop awaits per unit, beyond the two store phases
                # bounded inside it. The RESOLVER, not the raw field: `[dreamer]
                # max_unit_hold_seconds` ships unset, and `Config.
                # dreamer_max_unit_hold_seconds` derives a number from this config's own
                # budgets rather than a literal that goes stale when one is raised.
                max_unit_hold_seconds=self.cfg.dreamer_max_unit_hold_seconds(),
            ))
        elif self.cfg.dreamer.enabled:
            # The Dreamer is ON and its cursor store could not be built —
            # a database wiring fault, not a flag. Loud, and NOT
            # `pragma: no cover`d: the only symptom otherwise is silence.
            log.error(
                "dreamer.enabled = true but no watermark store was built, so "
                "the Dreamer's clock cannot start and no adaptor will ever "
                "run. Check the database wiring."
            )
        else:
            log.info("dream loop disabled (dreamer.enabled = false)")
        # The cognition loop's wake evaluator: it records wakes and hands each
        # to the Cognition session, the one background writer (DEC-277).
        # `None` under `[cognition] enabled = false`.
        if deps.wake_evaluator is not None:
            # BEFORE the loop starts, and awaited, for the reason the watermarks
            # above are: whatever a previous process left open is released before
            # a tick reads it, so a restart never holds a unit for the rest of a
            # session timeout. Never raises (it counts and logs); a wake it could
            # not release expires on its own clock.
            await deps.wake_evaluator.reclaim_orphans(
                process_started_ms=int(started_at * 1000),
            )
            deps.background.spawn(wake.wake_loop(
                deps.wake_evaluator, deps.metrics,
                tick_seconds=deps.wake_tick_seconds,
            ))
        else:
            log.info("cognition loop disabled ([cognition] enabled = false)")
        # The OAuth keepalive (DEC-308): HERE and not in composition, because
        # the eval subject composes the same graph and must not spend the
        # deployment's rotating refresh tokens. `None` with image generation
        # off or no lane token set.
        if deps.oauth_keepalive is not None:
            deps.oauth_keepalive.start(deps.background.spawn)
        # The social stream's poller, HERE for the keepalive's reason: the
        # eval subject composes the same graph and must not poll through the
        # deployment's Instagram login. `None` under `[social.poll] enabled =
        # false`, the shipped default.
        if deps.social_poller is not None:
            deps.background.spawn(deps.social_poller.run())
            if deps.social_perception is not None:
                deps.background.spawn(deps.social_perception.run())
        else:
            log.info("social poller disabled ([social.poll] enabled = false)")
        # A story frame's bytes go at their bound even with the poller off.
        if deps.story_expiry is not None:
            deps.background.spawn(deps.story_expiry.run())
        else:
            log.info("story media expiry off ([social] story_expiry_enabled = false)")
        # The computer session sweeper: composition builds it only while
        # `[computer] enabled`.
        if deps.computer_sweeper is not None:
            deps.background.spawn(runtime.computer_sweep_loop(
                deps.computer_sweeper, deps.metrics,
                interval_seconds=deps.computer_sweep_seconds,
            ))
        # The timed probe of computerd (DEC-261), the same flag.
        if deps.computer_probe is not None:
            deps.background.spawn(runtime.computer_probe_loop(
                deps.computer_probe, deps.metrics,
                interval_seconds=COMPUTER_PROBE_INTERVAL_SECONDS,
            ))
        deps.background.spawn(runtime.debounce_sweep_loop(
            deps.debounce, deps.metrics,
            interval_seconds=DEBOUNCE_SWEEP_INTERVAL_SECONDS,
            ttl_seconds=DEBOUNCE_SWEEP_TTL_SECONDS,
        ))
        # The participation occasion's clock: `deps.participation_trigger` is
        # None unless `[participation] enabled` AND composition built a fire
        # path. The flag alone gates nothing, matching the dream clock's "the
        # flag is what decides, not a widened interval" shape.
        if deps.participation_trigger is not None:
            deps.background.spawn(runtime.participation_loop(
                deps.participation_trigger, deps.metrics,
                tick_seconds=PARTICIPATION_TICK_SECONDS,
                sweep_ttl_seconds=PARTICIPATION_SWEEP_TTL_SECONDS,
            ))
        # EVERY enabled adapter, not just Signal's: one that never had
        # `listen()` called would be present in `/health` yet deaf. Each
        # platform gets its own supervised task, so one wedged Gateway cannot
        # take Signal down with it.
        adapter_tasks = runtime.start_adapters(
            deps.background,
            [
                runtime.SupervisedAdapter(
                    platform=adapter.platform,
                    listen=functools.partial(
                        adapter.listen, on_message=deps.dispatcher.on_message
                    ),
                )
                for adapter in deps.registry
            ],
            deps.metrics,
        )
        try:
            await asyncio.gather(*adapter_tasks)
        finally:
            await deps.background.cancel_all()

    async def _adopt_pending(self) -> None:
        """Adapts `adoption.backfill`'s `AdoptionCounts` return to the
        `Coroutine[Any, Any, None]` `BackgroundTasks.spawn` accepts. The counts
        are for callers that want them (tests); production reads the log line
        and the two `nora_*adoption*` counters."""
        deps = self.deps
        await adoption.backfill(
            audit=deps.audit, memory=deps.memory, persons=deps.persons,
            metrics=deps.metrics, media_join=deps.media_join,
            start_delay_seconds=ADOPTION_START_DELAY_SECONDS,
        )

    async def _update_last_event_age(self) -> None:
        """Publish adapter liveness: the pinned Signal series, and one series
        per platform.

        `nora_last_event_age_seconds` stays UNLABELLED and stays Signal's.
        Adding a label changes a series' IDENTITY: every dashboard or alert
        selecting it by bare name would stop matching, silently. (`deploy/
        watchdog.sh` itself reads `/health`'s `sse` block rather than this
        metric, and alerts rather than restarting.) The per-platform answer
        goes to its own series instead, which is append-only.
        """
        recorder = self.deps.health_recorder
        while True:
            await asyncio.sleep(10.0)
            for adapter in self.deps.registry:
                try:
                    health = await adapter.health()
                except Exception:
                    # A gauge never takes the loop down: one adapter's failing
                    # health() must not stop the others being published.
                    log.exception("health() failed for adapter %s", adapter.platform)
                    if recorder is not None:
                        await recorder.unknown(adapter.platform)
                    continue
                # The same sample, recorded as a transition when the state changed:
                # source health is Record.
                if recorder is not None:
                    await recorder.sample(health)
                age = health.last_event_age_seconds
                if age is None:
                    continue
                self.deps.metrics.gauge(
                    "nora_adapter_last_event_age_seconds", age, platform=adapter.platform,
                )
                if adapter.platform == "signal":
                    self.deps.metrics.gauge("nora_last_event_age_seconds", age)

    @staticmethod
    async def _record_revisions(recorder: RevisionRecorder, *, started_at: datetime) -> None:
        await recorder.record_start(started_at=started_at)
        await recorder.watch()

    async def shutdown(self) -> None:
        # The shadow Cognition sessions first: they hold the model client and
        # the database this method closes below, and a session cancelled here
        # records itself `cancelled` (its stimuli stay pending) instead of
        # vanishing with its wake reading «in flight». The evaluator's loop was
        # already cancelled by `run`; `CognitionShutdown` is idempotent.
        if self.deps.cognition_shutdown is not None:
            await self.deps.cognition_shutdown()
        # Sub-agent jobs in flight: cancelled before the client and the database close. Their
        # rows are failed by the next boot's recovery and the room is told.
        if self.deps.subagent_backend is not None:
            await self.deps.subagent_backend.shutdown()
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await self.deps.client.close()
        for client in self.deps.model_clients:
            await client.close()
        await self.deps.audit.aclose()


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface: the daemon (no subcommand) plus the read-only control
    plane (`groups`, `persons pending`).

    `--config` works for the daemon (`python -m nora`, `python -m nora
    --config other.toml`) because the subparsers are not `required`. It is
    also repeated on every subcommand through `_config_parent`, with
    `default=argparse.SUPPRESS` so that a subcommand which does NOT carry the
    flag leaves the top-level value in place instead
    of overwriting it with a second default. That is what makes both
    `nora --config c.toml groups` and the more natural `nora groups --config
    c.toml` work.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="nora")
    parser.add_argument("--config", default="config.toml")
    subparsers = parser.add_subparsers(dest="command")
    cli.add_subcommands(subparsers, parent)
    return parser


class _Serving(Protocol):
    async def run(self) -> None: ...

    async def shutdown(self) -> None: ...


async def run_until_stopped(
    app: _Serving, *, signals: Sequence[signal.Signals] = STOP_SIGNALS,
) -> None:
    """Run `app` until it ends or a stop signal arrives, then shut it down.

    Without a SIGTERM handler the process dies on the signal without
    unwinding: `finally` never runs, so `App.shutdown` (and with it
    `CognitionShutdown`, which cancels the sessions and records their wakes)
    would be reachable only by SIGINT. `docker stop` sends SIGTERM, so a
    restart would kill sessions mid-flight and leave their wakes open to hold
    their units for the rest of the session timeout. (`uv run`, the image's
    entrypoint, forwards SIGTERM to the process it spawns.)

    The first signal cancels `app.run()`, which stops the adapters and every
    background loop (so no NEW wake starts) and returns to `finally:
    app.shutdown()`. The handlers are removed on the first signal, so a SECOND one
    takes the default action and ends the process at once: an operator who
    cannot wait for the grace can still stop it, and `docker stop`'s SIGKILL after
    `stop_grace_period` is the backstop for a shutdown that hangs."""
    loop = asyncio.get_running_loop()
    serving = asyncio.ensure_future(app.run())
    stopping: list[signal.Signals] = []

    def _stop(received: signal.Signals) -> None:
        stopping.append(received)
        log.warning("received %s: stopping (a second signal ends the process at once)",
                    received.name)
        for sig in signals:
            loop.remove_signal_handler(sig)
        serving.cancel()

    for sig in signals:
        loop.add_signal_handler(sig, _stop, sig)
    try:
        await serving
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        await app.shutdown()


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "func", None) is not None:
        return cli.run(args)

    async def _run() -> None:
        app = await build_app(Path(args.config))
        await run_until_stopped(app)

    try:
        asyncio.run(_run())
    except StartupError as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
