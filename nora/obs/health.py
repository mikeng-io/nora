"""GET /health and GET /metrics (spec C.9)."""

from __future__ import annotations

import importlib.metadata
import time

from aiohttp import web

from ..store import db


class LLMState:
    """Tiny mutable holder the handler updates after a successful invocation."""

    def __init__(self) -> None:
        self.last_success_at: float | None = None


def _strands_version() -> str:
    return importlib.metadata.version("strands-agents")


def _sum_series(metrics, name: str) -> float:
    return sum(v for (n, _labels), v in metrics.snapshot().items() if n == name)


def build_health_app(
    *, adapter, db_path, honcho, persona_store, metrics, started_at: float,
    llm_state: LLMState,
) -> web.Application:
    app = web.Application()
    app["adapter"] = adapter
    app["db_path"] = db_path
    app["honcho"] = honcho
    app["persona_store"] = persona_store
    app["metrics"] = metrics
    app["started_at"] = started_at
    app["llm_state"] = llm_state

    async def health(request: web.Request) -> web.Response:
        a = request.app
        healthy = True

        try:
            sse = await a["adapter"].health()
        except Exception:                      # noqa: BLE001 — a health check never raises
            sse = {"connected": False}
        if not sse.get("connected", False):
            healthy = False

        try:
            stats = await db.stats(a["db_path"])
            sqlite = {"reachable": True, "rows": stats["messages"]}
        except Exception:                      # noqa: BLE001
            sqlite = {"reachable": False}
            healthy = False

        try:
            reachable = (await a["honcho"].health()).reachable
        except Exception:                      # noqa: BLE001
            reachable = False
        honcho_health = {"reachable": reachable}
        if not reachable:
            healthy = False

        try:
            persona_sha256 = a["persona_store"].get().sha256
        except Exception:                      # noqa: BLE001
            persona_sha256 = None
            healthy = False

        body = {
            "sse": sse,
            "sqlite": sqlite,
            "honcho": honcho_health,
            "omniroute": {"last_success_at": a["llm_state"].last_success_at},
            "errors_since_restart": _sum_series(a["metrics"], "nora_errors_total"),
            "uptime_seconds": time.time() - a["started_at"],
            "persona_sha256": persona_sha256,
            "strands_version": _strands_version(),
        }
        return web.json_response(body, status=200 if healthy else 503)

    async def metrics_endpoint(request: web.Request) -> web.Response:
        text = request.app["metrics"].render()
        return web.Response(text=text, headers={"Content-Type": "text/plain; version=0.0.4"})

    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics_endpoint)
    return app


async def serve(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    # health/metrics must be reachable from outside the container.
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    await site.start()
    return runner
