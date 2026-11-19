"""Generated offer lookup at the scoped computerd wire boundary."""

from __future__ import annotations

import json
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from uuid import uuid4

from aiohttp import WSMsgType, web

from nora.domain.computer import computer_unit_scope

OFFER_LOOKUP = "offer_lookup"
OFFER_URL = "https://offers.example.org/item"
BROWSER_BINDING = "computer.base_url"
_TITLE = "EX4 offer lookup"
_TAB = "generated-tab"
_FRAME = "generated-frame"
_SELECT = [
    "select",
    "Branch",
    "",
    [[0, "Every branch", "every", True], [1, "Wan Chai", "wanchai", False]],
]
_CONTROLS: tuple[tuple[str, str, list[Any]], ...] = (
    ("button", "Show offers", ["button", "Show offers", "", []]),
    ("combobox", "Branch", _SELECT),
    ("button", "Check stock", ["button", "Check stock", "", []]),
)
_TEXT = (
    "Show offers",
    "Branch Every branch Wan Chai",
    "Branch Wan Chai Check stock",
    "EX4 at Wan Chai: HKD 399. Stock not published.",
)


class RoutingBrowserFixture:
    """One generated browser for an anchored eval using the default Nora unit."""

    def __init__(self, registry: Path) -> None:
        self.registry = registry
        self.scope = computer_unit_scope("nora")
        self.instance = uuid4().hex
        self.token_env = f"NORA_SUBJECT_COMPUTER_TOKEN_{self.instance}"
        self.token = token_urlsafe()
        self._url = "about:blank"
        self._has_tab = False
        self._loader = 0
        self._world = 0
        self._stage = 0

    def provision(self, endpoint: str) -> None:
        (self.registry / f"{self.scope}.json").write_text(
            json.dumps(
                {
                    "scope": self.scope,
                    "endpoint": endpoint,
                    "name": OFFER_LOOKUP,
                    "instance": self.instance,
                }
            )
        )

    def application(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/identity", self._identity)
        app.router.add_post("/v1/state", self._state)
        app.router.add_get("/v1/browser", self._browser)
        return app

    def _authorize(self, request: web.Request) -> dict[str, str]:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            raise web.HTTPUnauthorized()
        return {"X-Computerd-Instance": self.instance}

    async def _identity(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"scope": self.scope, "instance": self.instance, "name": OFFER_LOOKUP},
            headers=self._authorize(request),
        )

    async def _state(self, request: web.Request) -> web.Response:
        headers = self._authorize(request)
        if (await request.json()).get("scope") != self.scope:
            raise web.HTTPForbidden()
        return web.json_response(
            {
                "status": "ok",
                "files": [],
                "files_truncated": False,
                "tmux": [],
                "processes": [],
                "browser": {"running": True},
            },
            headers=headers,
        )

    async def _browser(self, request: web.Request) -> web.WebSocketResponse:
        headers = self._authorize(request)
        if (
            request.query.get("scope") != self.scope
            or request.headers.get("X-Computerd-Instance") != self.instance
        ):
            raise web.HTTPForbidden()
        socket = web.WebSocketResponse()
        socket.headers.update(headers)
        await socket.prepare(request)
        async for message in socket:
            if message.type is WSMsgType.TEXT:
                command = json.loads(message.data)
                try:
                    result = self._dispatch(command["method"], command.get("params", {}))
                    reply = {"result": result}
                except (KeyError, ValueError) as exc:
                    reply = {"error": {"code": -32602, "message": str(exc)}}
                await socket.send_json({"id": command["id"], **reply})
        return socket

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method in {
            "Target.setDiscoverTargets",
            "Page.enable",
            "DOM.enable",
            "Accessibility.enable",
            "Emulation.setDeviceMetricsOverride",
        }:
            return {}
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {"targetId": _TAB, "type": "page", "url": self._url, "title": _TITLE}
                ]
                if self._has_tab
                else []
            }
        if method == "Target.createTarget":
            self._has_tab = True
            return {"targetId": _TAB}
        if method == "Target.attachToTarget":
            return {"sessionId": "generated-session"}
        if method == "Target.closeTarget":
            self._has_tab = False
            return {"success": True}
        if method == "Page.navigate":
            if params["url"] != OFFER_URL:
                raise ValueError("URL is outside the generated offer fixture")
            self._loader += 1
            self._url = OFFER_URL
            self._stage = 0
            return self._frame()
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {**self._frame(), "id": _FRAME}}}
        if method == "Page.createIsolatedWorld":
            self._world += 1
            return {"executionContextId": self._world}
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"clientWidth": 1920, "clientHeight": 1080}}
        if method == "Accessibility.getFullAXTree":
            nodes = [self._node(101, "heading", _TITLE)]
            if self._stage < len(_CONTROLS):
                role, name, _ = _CONTROLS[self._stage]
                nodes.append(self._node(102 + self._stage, role, name))
            return {"nodes": nodes}
        if method == "Runtime.evaluate":
            value = {
                "location.href": self._url,
                "document.readyState": "complete",
                "document.title": _TITLE,
                "document.body ? document.body.innerText : ''": f"{_TITLE} {_TEXT[self._stage]}",
            }[params["expression"]]
            return {"result": {"type": "string", "value": value}}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": str(params["backendNodeId"])}}
        if method == "Runtime.callFunctionOn":
            return self._control(params)
        raise ValueError(f"Unsupported generated browser method {method}")

    def _frame(self) -> dict[str, str]:
        return {"frameId": _FRAME, "loaderId": str(self._loader)}

    @staticmethod
    def _node(backend: int, role: str, name: str) -> dict[str, Any]:
        return {
            "nodeId": str(backend),
            "ignored": False,
            "backendDOMNodeId": backend,
            "role": {"value": role},
            "name": {"value": name},
            "properties": [],
        }

    def _control(self, params: dict[str, Any]) -> dict[str, Any]:
        if "getComputedStyle" not in params["functionDeclaration"]:
            raise ValueError("Unsupported generated browser function")
        expected, action, option = (argument["value"] for argument in params["arguments"])
        backend = int(params["objectId"])
        active = self._stage < len(_CONTROLS) and backend == 102 + self._stage
        control = _CONTROLS[self._stage][2] if active else None
        if expected is None:
            return {"result": {"type": "object", "value": control}}
        transition = ("select", 1) if self._stage == 1 else ("click", None)
        applied = active and expected == control and (action, option) == transition
        if applied:
            self._stage += 1
        return {"result": {"type": "boolean", "value": applied}}
