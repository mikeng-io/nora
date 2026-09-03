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
import os
import sys
import time
from pathlib import Path

from . import config
from .app import adoption, cli, runtime
from .app.composition import Dependencies, StartupError, build_dependencies
from .config import ConfigError
from .obs.health import build_health_app, serve
from .obs.logging import heartbeat, setup_logging
from .obs.tracing import setup_tracing
from .web.api import register_web_routes

PRUNE_INTERVAL_SECONDS = 300.0
DEBOUNCE_SWEEP_INTERVAL_SECONDS = 300.0
DEBOUNCE_SWEEP_TTL_SECONDS = 3600.0
ADOPTION_START_DELAY_SECONDS = adoption.DEFAULT_START_DELAY_SECONDS

log = logging.getLogger("nora.__main__")

__all__ = ["App", "StartupError", "build_app", "build_parser", "main"]


async def build_app(config_path: Path) -> App:
    try:
        cfg = config.load(config_path)
    except ConfigError as exc:
        raise StartupError(str(exc)) from exc

    key = os.environ.get(cfg.llm.api_key_env, "")
    setup_logging(
        level=cfg.obs.logging.level,
        path=cfg.obs.logging.path,
        format=cfg.obs.logging.format,
        secrets=[key] if key else [],
    )
    setup_tracing(
        enabled=cfg.obs.tracing.enabled,
        exporter=cfg.obs.tracing.exporter,
        endpoint=cfg.obs.tracing.endpoint,
        headers_env=cfg.obs.tracing.headers_env,
    )

    deps = await build_dependencies(cfg)
    return App(cfg=cfg, deps=deps)


class App:
    def __init__(self, *, cfg, deps: Dependencies) -> None:
        self.cfg = cfg
        self.deps = deps
        self._health_runner = None

    # Convenience accessors matching the pre-phase-3 App's surface — tests and
    # the health app read these directly rather than reaching into `deps`.
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
        )
        register_web_routes(
            health_app, store=deps.audit, honcho=deps.memory, reminders=deps.reminders,
            persons=deps.persons, ai_peer=self.cfg.memory.ai_peer, started_at=started_at,
            games_client=deps.games_client,
            presence=deps.presence, presence_circles=deps.presence_circles,
            mobility=deps.mobility,
            skills_dir=deps.skills_dir, events=deps.events,
            doctrine_store=deps.doctrine_store,
        )
        self._health_runner = await serve(health_app, self.cfg.obs.metrics.port)

        deps.background.spawn(heartbeat(deps.metrics))
        deps.background.spawn(self._update_last_event_age())
        # Lazy adoption, spawned exactly like the prune sweep and for the same
        # reason: it writes to the record store, and the ingest path drops a
        # message it cannot audit (the shape dates from the SQLite era's
        # single-writer WAL; off the hot path is still right under Postgres).
        # Nothing awaits it — a slow or failing backfill must not delay the
        # adapters coming up.
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
        # The render ledger's own sweep (31 §3.1). It joins the loop above
        # rather than growing one — it is a working set on a horizon, which is
        # exactly what that loop already does for `messages` — and it is
        # UNCONDITIONAL on `store.prune_enabled`: that flag means "keep the
        # record in full", and this is not a record. It is a working set whose
        # rows stop being readable by design, so never pruning it would be a
        # table nobody decided the size of.
        if deps.rendered_claims is not None:
            deps.background.spawn(runtime.prune_loop(
                deps.rendered_claims, deps.metrics,
                interval_seconds=PRUNE_INTERVAL_SECONDS,
            ))
        # The Dreamer's clock (Task 11), the SAME conditional shape as the
        # retention sweep above: `[language] enabled = false` (the shipped
        # default — a new subsystem does not switch itself on) means
        # `composition.build_dependencies` built no consolidator at all
        # (`deps.language_consolidators == ()`), so there is nothing this
        # loop could tick even if spawned; the flag is what decides whether
        # it starts, not a widened interval.
        # **`[dreamer]` OR `[language]`, and the `or` is the fix.** The clock
        # belongs to the RUNTIME; `[language]` selects one tier's adaptors. Until
        # 2026-08-27 this read `language.enabled and ...`, so `[dreamer] enabled
        # = true` was a switch that gated nothing: the runtime, its adaptors,
        # budgets and instructions were all built and nothing ever ticked them.
        # It shipped that way — merged as "the Dreamer runtime is on", five days
        # with no `dream tick`, and the `elif` below logging the reason into a
        # file nobody reads.
        #
        # Turning `[language]` on to obtain a clock was the available workaround
        # and is the wrong fix: room-language's MEASURE verb does not exist yet
        # (28 §4.1, D34), so it would resume writing single-sighting lexeme rows
        # — the defect that produced two contradictory "big walk" rows. A tier
        # that must not run cannot be the price of a runtime that must.
        #
        # `deps.language_store is not None` STAYS, and is no longer the same
        # question: composition now builds that store under either flag because
        # it owns `consolidation_watermark`, the Dreamer's own cursor. So this
        # reads "the clock is wanted, and the thing that remembers where each
        # adaptor got to exists" — two conditions rather than one wearing a
        # tier's name.
        if (
            (self.cfg.dreamer.enabled or self.cfg.language.enabled)
            and deps.language_store is not None
        ):
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
                keys=lambda a: (
                    deps.workspace_scopes() if a.key_kind == "workspace"
                    else deps.language_scopes()
                ),
            )
            deps.background.spawn(runtime.dream_loop(
                deps.audit, deps.language_store, deps.language_consolidators,
                deps.metrics,
                scopes=deps.language_scopes,
                tick_seconds=self.cfg.language.tick_seconds,
                safety_lag=self.cfg.language.safety_lag,
                quiet_seconds=self.cfg.language.quiet_seconds,
                max_age_seconds=self.cfg.language.max_age_seconds,
                batch_limit=self.cfg.language.batch_limit,
                portrait=deps.portrait_synthesizer,
                instructions=deps.dreamer_instructions,
                # The second, adaptor-shaped schedule (substrate plan Task
                # 2.8). Empty under `[dreamer] enabled = false`, in which case
                # `dream_loop` is byte-for-byte the loop that has been carrying
                # live traffic — which is what makes the cutover per-adaptor
                # rather than a big-bang switch.
                adaptors=deps.dream_adaptors,
                runtime=deps.dreamer_runtime,
                workspaces=deps.workspace_scopes,
                # Per-adaptor clocks (design/dreamer-agent-loop/32 §3.1) —
                # the fact-candidate extractor's daily interval/1000-row batch
                # live here, not in `[language]`'s shared numbers.
                schedules=deps.dream_schedules,
            ))
        elif self.cfg.dreamer.enabled:
            # **This branch changed meaning on 2026-08-27 and was rewritten
            # rather than deleted.** It used to catch `[dreamer] on, [language]
            # off` — a combination that is now ordinary and supported, and the
            # branch would have been dead if the condition above were the only
            # thing guarding it. It is not: composition builds the watermark
            # store under either flag, so reaching here with the Dreamer ON now
            # means that store could not be built at all.
            #
            # Left in place, and NOT `pragma: no cover`d, because the old
            # message is exactly what would mislead the next reader: it names a
            # cause that has been fixed, and an operator who greps for it during
            # an outage would go and turn `[language]` on — resuming the
            # single-sighting lexeme writes for a reason that no longer applies.
            # A stale diagnostic is worse than none; it aims the search.
            log.error(
                "dreamer.enabled = true but no watermark store was built, so "
                "the Dreamer's clock cannot start and no adaptor will ever "
                "run. This is NOT the old [language]-is-off case — that is "
                "supported now; the store is built under either flag. Check "
                "the database wiring."
            )
        else:
            log.info("dream loop disabled (language.enabled = false)")
        deps.background.spawn(runtime.debounce_sweep_loop(
            deps.debounce, deps.metrics,
            interval_seconds=DEBOUNCE_SWEEP_INTERVAL_SECONDS,
            ttl_seconds=DEBOUNCE_SWEEP_TTL_SECONDS,
        ))
        # EVERY enabled adapter, not just Signal's. `start_adapters` and
        # `supervise_adapter` were written generic over any number of them; this
        # was the one call site still pinning that to a single-element list, so
        # an enabled `DiscordAdapter` was built and identity-resolved at startup
        # and then never had `listen()` called — present in `/health`, deaf in
        # practice. Each platform gets its own supervised task, so one wedged
        # Gateway cannot take Signal down with it.
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
            metrics=deps.metrics, start_delay_seconds=ADOPTION_START_DELAY_SECONDS,
        )

    async def _update_last_event_age(self) -> None:
        """Publish adapter liveness: the pinned Signal series, and one series
        per platform.

        `nora_last_event_age_seconds` stays UNLABELLED and stays Signal's.
        It is the Signal-era liveness series, and adding a label changes a
        series' IDENTITY — every dashboard or alert selecting it by bare name
        would stop matching, silently, with no error anywhere. (`deploy/
        watchdog.sh` itself reads `/health`'s `sse` block rather than this
        metric, and ALERTS rather than restarting — checked, because an
        earlier version of this comment claimed otherwise.) The per-platform
        answer goes to its own series instead, which is append-only and breaks
        nothing.
        """
        while True:
            await asyncio.sleep(10.0)
            for adapter in self.deps.registry:
                try:
                    age = (await adapter.health()).last_event_age_seconds
                except Exception:
                    # A gauge never takes the loop down: one adapter's failing
                    # health() must not stop the others being published.
                    log.exception("health() failed for adapter %s", adapter.platform)
                    continue
                if age is None:
                    continue
                self.deps.metrics.gauge(
                    "nora_adapter_last_event_age_seconds", age, platform=adapter.platform,
                )
                if adapter.platform == "signal":
                    self.deps.metrics.gauge("nora_last_event_age_seconds", age)

    async def shutdown(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await self.deps.client.close()
        await self.deps.audit.aclose()


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface: the daemon (no subcommand) plus the read-only control
    plane (`groups`, `persons pending`).

    `--config` is unchanged for the daemon — `python -m nora` and `python -m
    nora --config other.toml` behave exactly as before, because the subparsers
    are not `required`. It is also repeated on every subcommand through
    `_config_parent`, with `default=argparse.SUPPRESS` so that a subcommand
    which does NOT carry the flag leaves the top-level value in place instead
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


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "func", None) is not None:
        return cli.run(args)

    async def _run() -> None:
        app = await build_app(Path(args.config))
        try:
            await app.run()
        finally:
            await app.shutdown()

    try:
        asyncio.run(_run())
    except StartupError as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
