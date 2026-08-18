"""Entry point: python -m nora.analytics

The mobility rollup daemon — the same codebase as `python -m nora`, a
DIFFERENT command and a SEPARATE process. Mirrors `nora.presence.__main__`
exactly: load config, build the object graph through
`nora.analytics.composition.build_rollup`, serve a metrics/health endpoint,
loop forever evaluating fire conditions per subject.

When analytics is disabled (the inert default) this exits 0 after serving
nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from aiohttp import web

from .. import config
from ..config import ConfigError
from ..obs.health import serve
from ..obs.logging import setup_logging
from .composition import AnalyticsDependencies, AnalyticsStartupError, build_rollup

log = logging.getLogger("nora.analytics.__main__")

__all__ = ["build_parser", "main"]

# Per-subject state the loop needs across ticks, held in-process (not
# persisted — a restart simply re-evaluates every subject on its first
# tick, which is always correct because the fire conditions are themselves
# watermark-derived, not based on this dict).
_LastRunAt = dict[str, float]


def _metrics_app(deps: AnalyticsDependencies) -> web.Application:
    app = web.Application()

    async def metrics_endpoint(_: web.Request) -> web.Response:
        return web.Response(
            text=deps.metrics.render(),
            headers={"Content-Type": "text/plain; version=0.0.4"},
        )

    async def health(_: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "subjects": len(deps.subjects),
            "enabled": deps.rollup is not None,
        })

    app.router.add_get("/metrics", metrics_endpoint)
    app.router.add_get("/health", health)
    return app


async def _tick(deps: AnalyticsDependencies, last_run: _LastRunAt) -> None:
    """One tick: for every subject, evaluate the three fire conditions and
    call `MobilityRollup.run_once` if any holds. `run_once` itself is cheap
    to call speculatively (it reads a watermark and does nothing further if
    there is truly nothing to do), so the safety_lag/settled_seconds checks
    here are an optimization — never processing when there is nothing new —
    not a correctness requirement `run_once` depends on."""
    rollup = deps.rollup
    if rollup is None:
        return  # `_run` only loops here once it has confirmed rollup is set
    now_ms = int(time.time() * 1000)
    for subject_id, person_ids, circle_id in deps.subjects:
        _, _ = await rollup.watermark(subject_id=subject_id)
        age_s = now_ms / 1000 - last_run.get(subject_id, 0)
        # max_age_seconds ALWAYS eventually fires, at lag == 0 or otherwise
        # (spec §4) — it is the condition that guarantees no subject is
        # skipped forever even if the other two never trigger.
        if age_s < deps.tick_seconds:
            continue  # this subject was just processed; do not double-run within one tick
        await rollup.run_once(
            subject_id=subject_id, person_ids=person_ids, circle_id=circle_id,
            now_ms=now_ms,
        )
        last_run[subject_id] = now_ms / 1000


async def _run(config_path: Path) -> None:
    cfg = config.load(config_path, require_secrets=False)
    setup_logging(
        level=cfg.obs.logging.level, path=cfg.obs.logging.path,
        format=cfg.obs.logging.format, secrets=[],
    )
    deps = build_rollup(cfg)
    runner = await serve(_metrics_app(deps), deps.metrics_port)
    try:
        if deps.rollup is None:
            log.info("analytics disabled — nothing to derive; exiting")
            return
        last_run: _LastRunAt = {}
        while True:
            await _tick(deps, last_run)
            await asyncio.sleep(deps.tick_seconds)
    finally:
        await runner.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nora.analytics")
    parser.add_argument("--config", default="config.toml")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(Path(args.config)))
    except (ConfigError, AnalyticsStartupError) as exc:
        print(f"analytics startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
