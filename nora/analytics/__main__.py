"""Entry point: python -m nora.analytics

The Rollup daemon — the same codebase as `python -m nora`, a DIFFERENT
command and a SEPARATE process. Mirrors `nora.presence.__main__`: load
config, build the object graph through `nora.analytics.composition.
build_rollup`, serve a metrics/health endpoint, and every tick run the whole
Rollup on the current build to the watermark `now - grace_seconds`. On start,
and whenever the pointer names a build of other code or parameters, it
rebuilds first.

`python -m nora.analytics rebuild` builds the Rollup again from the Record
into a new build, switches the pointer to it, and exits.

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

# When a subject's fold last completed WITHOUT raising, and when a subject was
# first seen (this process's first tick that reached it). Both hold wall-clock
# seconds and back `nora_analytics_rollup_age_seconds` — the same shape
# `nora.presence.collector`'s `_last_success_ms` / `_first_seen_ms` pair uses
# (`_publish_liveness`'s docstring): a subject that has never yet succeeded
# still emits a CLIMBING age from when it was first attempted, rather than no
# series at all. The wall clock here is for liveness only; it never enters a
# Rollup row.
_LastSuccessAt = dict[str, float]


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
            "units": len(deps.units),
            "enabled": deps.runner is not None,
            "config": deps.reload_files.status() if deps.reload_files else {},
        })

    app.router.add_get("/metrics", metrics_endpoint)
    app.router.add_get("/health", health)
    return app


async def _tick(
    deps: AnalyticsDependencies, last_success: _LastSuccessAt,
    first_seen: _LastSuccessAt,
) -> None:
    """One tick: run the whole Rollup on the current build to the watermark
    `now - grace_seconds` (`RollupRunner.run`). The wall clock chooses WHICH
    watermark to build to, never what is computed at it: the grace keeps a
    row whose transaction committed a moment late inside the watermark
    rather than behind it forever.

    `ensure_build` first: a rebuild started elsewhere (`python -m
    nora.analytics rebuild`) may have switched the pointer, and a build of
    other code or parameters is rebuilt, not extended.

    Isolation: one subject's failure neither aborts
    the sweep nor advances THAT subject's fold (its commit never happened),
    so it retries next tick — every other subject still gets processed, and
    the daemon (and its `/metrics` endpoint) stays up."""
    runner = deps.runner
    if runner is None:
        return  # `_run` only loops here once it has confirmed the runner is set
    now_s = time.time()
    through_ms = int((now_s - deps.grace_seconds) * 1000)
    failed: set[str] = {s.subject_id for s in deps.subjects}
    try:
        build_id = await runner.ensure_build(through_ms=through_ms)
        report = await runner.run(build_id=build_id, through_ms=through_ms)
        failed = set(report.failed)
    except Exception:
        log.exception("analytics rollup run failed — retried next tick")
    for subject in deps.subjects:
        sid = subject.subject_id
        first_seen.setdefault(sid, now_s)
        if sid not in failed:
            last_success[sid] = now_s
        # Computed fresh every tick, success or failure — never hardcoded to
        # 0.0, or the gauge could never climb to reveal «up but not
        # deriving». Baseline is the last SUCCESS, falling back to
        # first-seen for a subject that has never yet succeeded.
        baseline = last_success.get(sid, first_seen[sid])
        deps.metrics.gauge(
            "nora_analytics_rollup_age_seconds", max(0.0, now_s - baseline), subject=sid,
        )


async def _run(config_path: Path, *, rebuild: bool = False) -> None:
    cfg = config.load(config_path, require_secrets=False)
    setup_logging(
        level=cfg.obs.logging.level,
        format=cfg.obs.logging.format, secrets=[],
    )
    deps = build_rollup(cfg)
    if rebuild:
        if deps.runner is None:
            log.info("analytics disabled — nothing to rebuild")
            return
        through_ms = int((time.time() - deps.grace_seconds) * 1000)
        build_id = await deps.runner.rebuild(through_ms=through_ms)
        log.info("rebuilt the Rollup as build %d to %d and switched to it", build_id, through_ms)
        return
    runner = await serve(_metrics_app(deps), deps.metrics_port)
    reload_task = asyncio.create_task(deps.reload_files.run()) if deps.reload_files else None
    try:
        if deps.runner is None:
            log.info("analytics disabled — nothing to derive; exiting")
            return
        last_success: _LastSuccessAt = {}
        first_seen: _LastSuccessAt = {}
        while True:
            await _tick(deps, last_success, first_seen)
            await asyncio.sleep(deps.tick_seconds)
    finally:
        if reload_task is not None:
            reload_task.cancel()
            await asyncio.gather(reload_task, return_exceptions=True)
        await runner.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nora.analytics")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument(
        "command", nargs="?", choices=("run", "rebuild"), default="run",
        help="run: the daemon (default); rebuild: build the Rollup again from "
        "the Record into a new build, switch to it, and exit",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(Path(args.config), rebuild=args.command == "rebuild"))
    except (ConfigError, AnalyticsStartupError) as exc:
        print(f"analytics startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
