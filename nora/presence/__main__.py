"""Entry point: python -m nora.presence

The presence collector daemon — the same codebase as `python -m
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
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import config
from ..config import ConfigError
from ..obs.health import config_staleness, serve
from ..obs.logging import setup_logging
from ..obs.metrics import Metrics
from .composition import PresenceStartupError, build_collector

log = logging.getLogger("nora.presence.__main__")

__all__ = ["build_parser", "main"]


def _metrics_app(
    metrics: Metrics, collector: Any = None, refresh_timeout_seconds: float = 8.0,
    identity_file: str | None = None, started_at: float | None = None,
    identity_status: Any = None,
) -> web.Application:
    """A minimal /metrics + /health app for the collector process — it has no
    adapter or record store to report on, only its own metrics registry (which
    carries the liveness heartbeat and the canary counters).

    Plus `POST /refresh?person_id=…` (DEC-67): Nora's `where_is` calls it to force
    a LIVE on-demand reading for one person — the collector owns Life360 and the
    DB write, so `where_is` stays a pure DB reader and no Life360 credential lands
    in Nora. 200 once a fresh fix is written, 408 on timeout / nothing fresher,
    503 when collection is disabled. Best-effort: never 500s (a failed refresh
    leaves `where_is` reading the last stored fix).

    ACCEPTED RISK — no rate-limit, no coalescing (DEC-69).
    Each call fires an independent Life360 location request, so N simultaneous
    «where_is Miles» tags would fire N pings for one member (and a flapping caller
    could hammer Life360 toward its 429 ban). This is accepted, not overlooked,
    because the surface is INTERNAL: the endpoint binds the compose network only,
    is reachable solely by Nora (not the internet), and `where_is`'s own budget
    (2/tag) bounds the per-tag rate. The collector's `auth_failures` back-off is
    the real ban guard (a 429 stops the loop); this endpoint adds no throttle on
    top. A second internal caller would need a per-person cooldown and
    concurrent-request coalescing."""
    app = web.Application()

    async def metrics_endpoint(_: web.Request) -> web.Response:
        return web.Response(
            text=metrics.render(),
            headers={"Content-Type": "text/plain; version=0.0.4"},
        )

    async def health(_: web.Request) -> web.Response:
        # The LOADED-config summary: the circles this collector actually polls
        # + whether identity.toml changed on disk since it started. `1 circle,
        # 1 member` or `identity.stale=true` explains a member who gets no fix,
        # visible in one GET.
        body: dict[str, object] = {"status": "ok"}
        if collector is not None:
            body["circles"] = collector.circle_summary()
        if identity_file is not None:
            config_state = config_staleness(
                {"identity": identity_file}, started_at or 0.0
            )
            if identity_status is not None:
                config_state["identity"].update(identity_status())
            body["config"] = config_state
        return web.json_response(body)

    async def refresh(request: web.Request) -> web.Response:
        person_id = request.query.get("person_id", "")
        if collector is None:
            return web.json_response({"status": "unavailable"}, status=503)
        if not person_id:
            return web.json_response({"status": "bad_request"}, status=400)
        try:
            fresh = await collector.refresh_member(
                person_id=person_id, timeout_seconds=refresh_timeout_seconds,
            )
        except Exception:  # never 500 — a broken refresh degrades to the stored fix
            log.exception("on-demand refresh failed for a person")
            return web.json_response({"status": "error"}, status=502)
        return web.json_response(
            {"status": "fresh" if fresh else "stale"}, status=200 if fresh else 408,
        )

    app.router.add_get("/metrics", metrics_endpoint)
    app.router.add_get("/health", health)
    app.router.add_post("/refresh", refresh)
    # Application-HTTP coverage (ADR 0034); /health + /metrics polls excluded.
    try:
        from nora.obs.tracing import instrument_aiohttp_app

        instrument_aiohttp_app(app)
    except Exception:
        pass
    return app


async def _run(config_path: Path) -> None:
    cfg = config.load(config_path, require_secrets=False)
    setup_logging(
        level=cfg.obs.logging.level,
        format=cfg.obs.logging.format,
        # The collector's own credentials, redacted the way `nora.__main__`
        # redacts the LLM key. The username is included on purpose: it is an
        # email address — an identifier, exactly what this repo keeps out of
        # log files.
        secrets=[
            value
            for value in (
                cfg.credentials.get(cfg.presence.client_credential_env, ""),
                cfg.credentials.get(cfg.presence.username_env, ""),
                cfg.credentials.get(cfg.presence.password_env, ""),
                cfg.credentials.get(cfg.presence.auth_token_env, ""),
                cfg.credentials.get(cfg.presence.geocode_api_key_env, ""),
                # The HK-gov token rides the query string (the leakiest position),
                # so the scrub safety net must cover it too.
                cfg.credentials.get(cfg.presence.hkgov_token_env, ""),
            )
            if value
        ],
    )
    # Same observability bootstrap as the main process (ADR 0034): errors and
    # traces were never wired here, so this process's failures were invisible
    # outside its own log. Never load-bearing — a bad endpoint degrades, it
    # does not stop the collector.
    from nora.obs.errors import setup_errors
    from nora.obs.tracing import build_sampler

    _entry = None
    try:
        from nora.infra.version import VersionManifest

        _entry = VersionManifest().current().entry
    except Exception:
        pass
    _release = _entry.version if _entry is not None else ""
    setup_errors(
        enabled=cfg.obs.errors.enabled, dsn_env=cfg.obs.errors.dsn_env,
        credentials=cfg.credentials, environment=cfg.obs.errors.environment,
        release=_release,
    )
    if cfg.obs.tracing.enabled:
        try:
            from nora.agent.telemetry import setup_agent_tracing

            setup_agent_tracing(
                sampler=build_sampler(), service_name="presence",
                exporter=cfg.obs.tracing.exporter,
                endpoint=cfg.obs.tracing.endpoint,
                headers_env=cfg.obs.tracing.headers_env,
                credentials=cfg.credentials,
                service_version=_release,
                environment=cfg.obs.errors.environment,
            )
        except Exception:
            log.warning("tracing setup failed; continuing without tracing", exc_info=True)
    deps = build_collector(cfg)
    runner = await serve(
        _metrics_app(
            deps.metrics, deps.collector, cfg.presence.refresh_timeout_seconds,
            identity_file=str(cfg.identity_file), started_at=time.time(),
            identity_status=deps.identity_reload.status if deps.identity_reload else None,
        ),
        deps.metrics_port,
    )
    reload_task = asyncio.create_task(deps.identity_reload.run()) if deps.identity_reload else None
    try:
        if deps.collector is None:
            log.info("presence disabled — nothing to collect; exiting")
            return
        await deps.collector.run()
    finally:
        if reload_task is not None:
            reload_task.cancel()
            await asyncio.gather(reload_task, return_exceptions=True)
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
