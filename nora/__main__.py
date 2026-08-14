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
        health_app = build_health_app(
            adapter=deps.adapter, adapters=deps.registry.adapters,
            store=deps.audit, honcho=deps.memory,
            doctrine_store=deps.doctrine_store, metrics=deps.metrics,
            started_at=time.time(), llm_state=deps.llm_state,
            identity_file=self.cfg.identity_file, policy_file=self.cfg.policy_file,
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
