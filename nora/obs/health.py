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
    this process started — the tell for the single-file bind-mount inode gotcha
    that silently served a stale identity.toml/policy.toml TWICE on 2026-08-08
    (a recreate is needed, not a restart). `stale=True` means: the file the
    operator edited is NOT the one this process loaded — recreate the container.
    Never raises: a missing/unreadable file reports `mtime=None, stale=False`."""
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
) -> web.Application:
    app = web.Application()
    app["adapter"] = adapter
    # Every platform's adapter, keyed by name — ADDITIVE beside `adapter`, which
    # stays the Signal one because `sse` is a pinned top-level contract
    # (`deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`;
    # `tests/test_health.py::test_health_sse_key_set_is_pinned`). Without this a
    # second platform could be wedged — Gateway down, every Discord message
    # dropped — while `/health` reported 200 on Signal alone, which is exactly
    # the silent wiring gap R-5 exists to make loud.
    # `is not None`, so an explicitly empty mapping means "no platforms" rather
    # than silently falling back to `adapter` — the fallback is for callers
    # predating this parameter, not a way to mean nothing.
    if adapters is not None:
        app["adapters"] = dict(adapters)
    else:
        app["adapters"] = {adapter.platform: adapter} if adapter is not None else {}
    app["store"] = store
    app["honcho"] = honcho
    app["doctrine_store"] = doctrine_store
    app["metrics"] = metrics
    app["started_at"] = started_at
    app["llm_state"] = llm_state
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

        # One entry per enabled platform, same three facts `sse` carries. A
        # platform that cannot report at all is recorded disconnected rather
        # than omitted — an absent key would read as "not enabled" and hide
        # exactly the outage this block exists to surface. Every enabled
        # platform counts toward `healthy`: Nora deaf on Discord is not a
        # healthy Nora just because Signal happens to be up.
        platforms = {}
        for name, plat_adapter in a["adapters"].items():
            try:
                plat_health = await plat_adapter.health()
                entry = {
                    "connected": plat_health.connected,
                    "last_event_age_seconds": plat_health.last_event_age_seconds,
                    # M0 (design/instagram-adoption/01 §Degradation). A
                    # degraded adapter is registered, answers no sends and
                    # emits no events — and without this field it is
                    # indistinguishable from a quiet room, which is the
                    # failure the degradation path was allowed to exist for.
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
            # A DEGRADED platform does not make Nora unhealthy, and that is the
            # whole point of the state. An Instagram checkpoint is a normal
            # overnight event; flipping /health to 503 for it would fail the
            # container healthcheck and take Signal and Discord down with it —
            # the exact outcome M0 exists to prevent, arriving one layer later.
            # It is reported LOUDLY instead, which is what the field is for.
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
        # Staleness canary (obs review 2026-08-08): identity.toml/policy.toml are
        # bind-mounted and loaded ONCE at startup, and an atomic edit changes the
        # inode so a restart keeps serving the OLD file — a recreate is needed.
        # `config.<file>.stale=true` says the operator's edit hasn't taken; it
        # silently served a stale policy.toml AND identity.toml the same day.
        if a["config_files"]:
            body["config"] = config_staleness(a["config_files"], a["started_at"])
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
    # `access_log=None` — NO PER-REQUEST LINE FOR THIS SERVER, and it is the
    # single largest thing that was in the log.
    #
    # Measured over 37 days on the deployment: 205,099 log lines, of which
    # 108,441 (53%) were `aiohttp.access` at INFO. This server routes exactly
    # two paths — `/health` and `/metrics` — polled by a container healthcheck
    # and by whatever scrapes the metrics. One line per poll is not evidence of
    # anything: the health of this endpoint is the healthcheck's own verdict,
    # and how often it was scraped is a question for the scraper.
    #
    # It is the same judgement R-76 makes for traces one signal over — a
    # collector that is 97% plumbing cannot be read for the events that matter —
    # applied to the log. What it costs is the ability to see WHO called
    # `/metrics`, which is a question this file has never been asked.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # health/metrics must be reachable from outside the container.
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    await site.start()
    return runner
