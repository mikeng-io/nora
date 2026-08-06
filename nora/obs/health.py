"""GET /health and GET /metrics (spec C.9)."""

from __future__ import annotations

import importlib.metadata
import time

from aiohttp import web


class LLMState:
    """Tiny mutable holder the handler updates after a successful invocation."""

    def __init__(self) -> None:
        self.last_success_at: float | None = None


def _strands_version() -> str:
    return importlib.metadata.version("strands-agents")


def _sum_series(metrics, name: str) -> float:
    return sum(v for (n, _labels), v in metrics.snapshot().items() if n == name)


def build_health_app(
    *, adapter, store, honcho, doctrine_store, metrics, started_at: float,
    llm_state: LLMState,
) -> web.Application:
    app = web.Application()
    app["adapter"] = adapter
    app["store"] = store
    app["honcho"] = honcho
    app["doctrine_store"] = doctrine_store
    app["metrics"] = metrics
    app["started_at"] = started_at
    app["llm_state"] = llm_state

    async def health(request: web.Request) -> web.Response:
        a = request.app
        healthy = True

        try:
            adapter_health = await a["adapter"].health()
            sse = {
                "connected": adapter_health.connected,
                "last_event_age_seconds": adapter_health.last_event_age_seconds,
                "platform": adapter_health.platform,
            }
            # `account` comes from resolve_identity(), not a new AdapterHealth
            # field (Trap A, spec's platforms phase): identity and connectivity
            # are different concerns, and SignalAdapter.resolve_identity() is
            # memoized, so this costs a network round trip at most once per
            # process, not once per poll.
            try:
                identity = await a["adapter"].resolve_identity()
                sse["account"] = identity.primary
            except Exception:                  # noqa: BLE001 — a health check never raises
                sse["account"] = None
        except Exception:                      # noqa: BLE001 — a health check never raises
            sse = {"connected": False}
        if not sse.get("connected", False):
            healthy = False

        try:
            stats = await a["store"].stats()
            store = {"reachable": True, "rows": stats["messages"]}
        except Exception:                      # noqa: BLE001
            store = {"reachable": False}
            healthy = False

        try:
            reachable = (await a["honcho"].health()).reachable
        except Exception:                      # noqa: BLE001
            reachable = False
        honcho_health = {"reachable": reachable}
        if not reachable:
            healthy = False

        try:
            doctrine_sha256 = a["doctrine_store"].get().sha256
        except Exception:                      # noqa: BLE001
            doctrine_sha256 = None
            healthy = False

        body = {
            "sse": sse,
            "store": store,
            "honcho": honcho_health,
            "omniroute": {"last_success_at": a["llm_state"].last_success_at},
            "errors_since_restart": _sum_series(a["metrics"], "nora_errors_total"),
            "uptime_seconds": time.time() - a["started_at"],
            "doctrine_sha256": doctrine_sha256,
            "strands_version": _strands_version(),
        }
        return web.json_response(body, status=200 if healthy else 503)

    async def metrics_endpoint(request: web.Request) -> web.Response:
        # Deliberately not prometheus-client's own CONTENT_TYPE_LATEST: in this
        # version it advertises "version=1.0.0" even though generate_latest()
        # still renders the classic 0.0.4-style text format actually produced
        # below — using it here would be an accidental behaviour change this
        # task never asked for. This header is unchanged from before the
        # prometheus-client swap.
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
