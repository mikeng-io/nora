"""Registers GET /app/v1/status and GET /app/v1/memory onto an existing
aiohttp Application — same process/container as Nora (design spec §3).

Deliberately a `register_*` function taking an existing `web.Application`,
not a `build_*_app` returning a new one: these routes join the health app
nora/obs/health.py::build_health_app already builds and nora/__main__.py
already serves on :9100 — a second app would mean a second port, which the
spec's deployment section (§6) explicitly rules out.
"""

from __future__ import annotations

from aiohttp import web

from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore
from nora.persons import Persons
from nora.web.memory import build_memory_payload
from nora.web.status import build_status_payload


def register_web_routes(
    app: web.Application, *, store: RecordStore, honcho: MemoryStore,
    reminders: ReminderStore, persons: Persons, ai_peer: str, started_at: float,
) -> None:
    app["web_store"] = store
    app["web_honcho"] = honcho
    app["web_reminders"] = reminders
    app["web_persons"] = persons
    app["web_ai_peer"] = ai_peer
    app["web_started_at"] = started_at

    async def status(request: web.Request) -> web.Response:
        a = request.app
        body = await build_status_payload(
            store=a["web_store"], honcho=a["web_honcho"], reminders=a["web_reminders"],
            started_at=a["web_started_at"],
        )
        return web.json_response(body)

    async def memory(request: web.Request) -> web.Response:
        a = request.app
        body = await build_memory_payload(
            honcho=a["web_honcho"], persons=a["web_persons"], ai_peer=a["web_ai_peer"],
        )
        return web.json_response(body)

    app.router.add_get("/app/v1/status", status)
    app.router.add_get("/app/v1/memory", memory)
