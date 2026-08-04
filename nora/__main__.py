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
from .agent import factory
from .app import runtime
from .app.composition import Dependencies, StartupError, build_dependencies
from .config import ConfigError
from .obs.health import build_health_app, serve
from .obs.logging import heartbeat, setup_logging
from .obs.tracing import setup_tracing

PRUNE_INTERVAL_SECONDS = 300.0
DEBOUNCE_SWEEP_INTERVAL_SECONDS = 300.0
DEBOUNCE_SWEEP_TTL_SECONDS = 3600.0

log = logging.getLogger("nora.__main__")

__all__ = ["App", "StartupError", "build_app", "main"]


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
    def persona(self):
        return self.deps.persona

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
            adapter=deps.adapter, store=deps.audit, honcho=deps.memory,
            persona_store=deps.persona_store, metrics=deps.metrics,
            started_at=time.time(), llm_state=deps.llm_state,
        )
        self._health_runner = await serve(health_app, self.cfg.obs.metrics.port)

        deps.background.spawn(heartbeat(deps.metrics))
        deps.background.spawn(self._update_last_event_age())
        deps.background.spawn(runtime.prune_loop(
            deps.audit, deps.metrics, interval_seconds=PRUNE_INTERVAL_SECONDS,
        ))
        deps.background.spawn(runtime.debounce_sweep_loop(
            deps.debounce, deps.metrics,
            interval_seconds=DEBOUNCE_SWEEP_INTERVAL_SECONDS,
            ttl_seconds=DEBOUNCE_SWEEP_TTL_SECONDS,
        ))
        adapter_tasks = runtime.start_adapters(
            deps.background,
            [runtime.SupervisedAdapter(
                platform=deps.adapter.platform,
                listen=functools.partial(deps.adapter.listen, deps.dispatcher.on_event),
            )],
            deps.metrics,
        )
        try:
            await asyncio.gather(*adapter_tasks)
        finally:
            await deps.background.cancel_all()

    async def _update_last_event_age(self) -> None:
        while True:
            await asyncio.sleep(10.0)
            health = await self.deps.adapter.health()
            age = health.get("last_event_age_seconds")
            if age is not None:
                self.deps.metrics.gauge("nora_last_event_age_seconds", age)

    async def shutdown(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await factory.close_client()
        await self.deps.audit.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()

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
