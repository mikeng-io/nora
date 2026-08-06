"""Entry point: python -m nora.presence

The presence collector daemon (design/19 §4) — the same codebase as `python -m
nora`, a DIFFERENT command and a SEPARATE process. Thin by the same design as
`nora.__main__`: load config, build the object graph through
`nora.presence.composition.build_collector`, serve a metrics/health endpoint,
and run the per-circle loops until cancelled.

When presence is disabled (the inert default) the collector exits 0 after
serving nothing — a process an operator started before turning the feature on
should stop cleanly, not error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from aiohttp import web

from .. import config
from ..config import ConfigError
from ..obs.health import serve
from ..obs.logging import setup_logging
from .composition import PresenceStartupError, build_collector

log = logging.getLogger("nora.presence.__main__")

__all__ = ["build_parser", "main"]


def _metrics_app(metrics) -> web.Application:
    """A minimal /metrics + /health app for the collector process — it has no
    adapter or record store to report on, only its own metrics registry (which
    carries the liveness heartbeat and the canary counters, §4)."""
    app = web.Application()

    async def metrics_endpoint(_: web.Request) -> web.Response:
        return web.Response(
            text=metrics.render(),
            headers={"Content-Type": "text/plain; version=0.0.4"},
        )

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/metrics", metrics_endpoint)
    app.router.add_get("/health", health)
    return app


async def _run(config_path: Path) -> None:
    cfg = config.load(config_path, require_secrets=False)
    setup_logging(
        level=cfg.obs.logging.level,
        path=cfg.obs.logging.path,
        format=cfg.obs.logging.format,
        secrets=[],
    )
    deps = build_collector(cfg)
    runner = await serve(_metrics_app(deps.metrics), deps.metrics_port)
    try:
        if deps.collector is None:
            log.info("presence disabled — nothing to collect; exiting")
            return
        await deps.collector.run()
    finally:
        await runner.cleanup()
        if deps.store is not None:
            await deps.store.aclose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nora.presence")
    parser.add_argument("--config", default="config.toml")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(Path(args.config)))
    except (ConfigError, PresenceStartupError) as exc:
        print(f"presence startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
