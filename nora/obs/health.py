"""GET /health and GET /metrics (spec C.9)."""

from __future__ import annotations

import importlib.metadata
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web


def config_staleness(paths: dict[str, str | Path], started_at: float) -> dict:
    """For each named bind-mounted config file, whether it changed on disk AFTER
    this process started — the tell for the single-file bind-mount inode
    problem (a recreate is needed, not a restart). `stale=True` means: the file
    the operator edited is NOT the one this process loaded — recreate the
    container. Never raises: a missing/unreadable file reports
    `disk_mtime=None, stale=False`."""
    out: dict[str, dict] = {}
    for name, path in paths.items():
        try:
            mtime = os.path.getmtime(path)
            out[name] = {
                "disk_mtime": datetime.fromtimestamp(mtime, UTC).isoformat(),
                "stale": mtime > started_at,
            }
        except OSError:
            out[name] = {"disk_mtime": None, "stale": False}
    return out


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
    adapters=None,
    identity_file: str | Path | None = None, policy_file: str | Path | None = None,
    index_state=None, config_reload=None,
) -> web.Application:
    app = web.Application()
    app["adapter"] = adapter
    # Every platform's adapter, keyed by name, beside `adapter`, which is the
    # Signal one because `sse` is a pinned top-level contract
    # (`deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`;
    # `tests/test_health.py::test_health_sse_key_set_is_pinned`). Without the
    # per-platform map a wedged second platform would leave `/health` at 200 on
    # Signal alone (R-5).
    # `is not None`, so an explicitly empty mapping means "no platforms" rather
    # than falling back to `adapter`.
    if adapters is not None:
        app["adapters"] = dict(adapters)
    else:
        app["adapters"] = {adapter.platform: adapter} if adapter is not None else {}
    app["store"] = store
    app["honcho"] = honcho
    app["doctrine_store"] = doctrine_store
    app["metrics"] = metrics
    app["config_reload"] = config_reload
    app["started_at"] = started_at
    app["llm_state"] = llm_state
    # A zero-arg callable answering the memory index's `IndexState`
    # (`nora.infra.sql.memory_read.SqlMemoryIndex.index_state`), or None on a
    # deployment with no index to report.
    app["index_state"] = index_state
    # The bind-mounted, load-once config files — for the staleness canary below.
    app["config_files"] = {
        name: path for name, path in
        (("identity", identity_file), ("policy", policy_file)) if path is not None
    }

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
            # `account` comes from resolve_identity(), not an AdapterHealth
            # field: identity and connectivity are different concerns, and
            # SignalAdapter.resolve_identity() is memoized, so this costs a
            # network round trip at most once per process, not once per poll.
            try:
                identity = await a["adapter"].resolve_identity()
                sse["account"] = identity.primary
            except Exception:                  # noqa: BLE001 — a health check never raises
                sse["account"] = None
        except Exception:                      # noqa: BLE001 — a health check never raises
            sse = {"connected": False}
        if not sse.get("connected", False):
            healthy = False

        # One entry per enabled platform, same three facts `sse` carries. A
        # platform that cannot report at all is recorded disconnected rather
        # than omitted, since an absent key would read as "not enabled". Every
        # enabled platform counts toward `healthy`.
        platforms = {}
        for name, plat_adapter in a["adapters"].items():
            try:
                plat_health = await plat_adapter.health()
                entry = {
                    "connected": plat_health.connected,
                    "last_event_age_seconds": plat_health.last_event_age_seconds,
                    # A degraded adapter is registered, answers no sends and
                    # emits no events; without this field it is
                    # indistinguishable from a quiet room.
                    "degraded": plat_health.degraded,
                    "detail": plat_health.detail,
                }
                try:
                    entry["account"] = (await plat_adapter.resolve_identity()).primary
                except Exception:              # noqa: BLE001 — a health check never raises
                    entry["account"] = None
            except Exception:                  # noqa: BLE001 — a health check never raises
                entry = {"connected": False, "last_event_age_seconds": None, "account": None,
                         "degraded": False, "detail": ""}
            # A DEGRADED platform does not make Nora unhealthy: a 503 would
            # fail the container healthcheck and take the other platforms down
            # with it. It is reported in the entry instead.
            if not entry["connected"] and not entry.get("degraded"):
                healthy = False
            platforms[name] = entry

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
            "platforms": platforms,
            "store": store,
            "honcho": honcho_health,
            "omniroute": {"last_success_at": a["llm_state"].last_success_at},
            "errors_since_restart": _sum_series(a["metrics"], "nora_errors_total"),
            "uptime_seconds": time.time() - a["started_at"],
            "doctrine_sha256": doctrine_sha256,
            "strands_version": _strands_version(),
        }
        # The memory index's health (25): the state row's status
        # (`never`/`building`/`complete`) and the projection queue's length —
        # the queue holds only what is still unprojected, so its length IS
        # the index's health. Reported, never counted toward `healthy`: a
        # rebuild in progress must not flap the container healthcheck. A
        # failed read says `unavailable`, never a guessed status (R-27).
        if a["index_state"] is not None:
            try:
                state = await a["index_state"]()
                body["index"] = {"status": state.status, "queue": state.queue}
            except Exception:              # noqa: BLE001 — a health check never raises
                body["index"] = {"status": "unavailable"}
        # Staleness canary: identity.toml/policy.toml are bind-mounted and
        # loaded ONCE at startup, and an atomic edit changes the inode so a
        # restart keeps serving the OLD file; a recreate is needed.
        # `config.<file>.stale=true` says the operator's edit hasn't taken.
        if a["config_files"]:
            body["config"] = config_staleness(a["config_files"], a["started_at"])
            if a["config_reload"] is not None:
                for name, state in a["config_reload"]().items():
                    body["config"].setdefault(name, {}).update(state)
        return web.json_response(body, status=200 if healthy else 503)

    async def metrics_endpoint(request: web.Request) -> web.Response:
        # Deliberately not prometheus-client's own CONTENT_TYPE_LATEST: it
        # advertises "version=1.0.0" even though generate_latest() renders the
        # classic 0.0.4-style text format.
        text = request.app["metrics"].render()
        return web.Response(text=text, headers={"Content-Type": "text/plain; version=0.0.4"})

    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics_endpoint)
    return app


async def serve(app: web.Application, port: int) -> web.AppRunner:
    # `access_log=None`: no per-request line for this server. It routes only
    # `/health` and `/metrics`, polled by a healthcheck and a scraper, so an
    # access line per poll would dominate the log (cf. R-76). The cost is not
    # seeing who called `/metrics`.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # health/metrics must be reachable from outside the container.
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    await site.start()
    return runner
