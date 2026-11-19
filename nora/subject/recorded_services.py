"""Recorded HTTP responses at the isolated external-service boundary."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError
from nora.subject.model_gateway import ModelGateway
from nora.subject.routing_browser_fixture import BROWSER_BINDING, RoutingBrowserFixture

_OPTIONAL_WEB_BINDINGS = frozenset({
    "search.exa.url", "search.exa.contents_url",
    "search.tavily.url", "search.tavily.extract_url",
})


def _object(value: Any, required: set[str], allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - allowed:
        raise ValueError("invalid recorded service schema")
    return value


def _validate(data: Any) -> None:
    root = _object(data, {"version", "bindings", "services"}, {"version", "bindings", "services"})
    if type(root["version"]) is not int or root["version"] != 1:
        raise ValueError("unsupported recorded service version")
    bindings, services = root["bindings"], root["services"]
    if (
        not isinstance(bindings, dict)
        or not bindings
        or not isinstance(services, dict)
        or not services
    ):
        raise ValueError("invalid recorded service bindings")
    for name, sequence in services.items():
        if not isinstance(name, str) or not name or not isinstance(sequence, list):
            raise ValueError("invalid recorded service")
        for item in sequence:
            row = _object(
                item, {"method", "path", "response"},
                {"method", "path", "request_json", "response", "repeatable"},
            )
            method, path = row["method"], row["path"]
            if not isinstance(method, str) or not re.fullmatch(r"[A-Z]+", method):
                raise ValueError("invalid recorded method")
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                raise ValueError("invalid recorded path")
            parts = urlsplit(path)
            if parts.scheme or parts.netloc or parts.fragment or any(c.isspace() for c in path):
                raise ValueError("invalid recorded path")
            if "repeatable" in row and (
                type(row["repeatable"]) is not bool
                or (row["repeatable"] and (method != "GET" or "request_json" in row))
            ):
                raise ValueError("repeatable recordings require a bodyless GET")
            response = _object(row["response"], {"status"}, {"status", "headers", "json", "chunks"})
            status = response["status"]
            if type(status) is not int or not 200 <= status <= 599:
                raise ValueError("invalid recorded status")
            if "json" in response and "chunks" in response:
                raise ValueError("ambiguous recorded response")
            headers = response.get("headers", {})
            if not isinstance(headers, dict) or any(
                not isinstance(k, str)
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k)
                or not isinstance(v, str)
                or "\n" in v
                or "\r" in v
                or k.lower() in {"content-length", "transfer-encoding", "content-encoding"}
                for k, v in headers.items()
            ):
                raise ValueError("invalid recorded headers")
            if "chunks" in response and not isinstance(response["chunks"], list):
                raise ValueError("invalid recorded chunks")
    for binding, service in bindings.items():
        if (
            not isinstance(binding, str)
            or not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", binding)
            or not isinstance(service, str)
            or service not in services
        ):
            raise ValueError("invalid recorded service binding")
    if set(bindings.values()) != set(services):
        raise ValueError("unbound recorded service")


def _same(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _discord_guild(body: Any) -> str | None:
    if not isinstance(body, dict) or set(body) != {"op", "params"} or body["op"] != "channels":
        return None
    params = body["params"]
    if not isinstance(params, dict) or set(params) != {"guild_id"}:
        return None
    guild = params["guild_id"]
    return guild if isinstance(guild, str) and guild.strip() else None


def _background_discord(
    data: dict[str, Any], rows: Any, *, routing: bool,
) -> tuple[str | None, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("background Discord reads must be a list")
    if not rows:
        return None, {}
    bindings = data["bindings"]
    service = bindings.get("platforms.discord.base_url")
    if not routing or service is None or list(bindings.values()).count(service) != 1:
        raise ValueError("background Discord reads require a dedicated routing bridge")
    responses = {}
    for raw in rows:
        row = _object(raw, {"request_json", "response"}, {"request_json", "response"})
        guild = _discord_guild(row["request_json"])
        response = _object(row["response"], {"status", "json"}, {"status", "json"})
        if (guild is None or guild in responses
                or type(response["status"]) is not int
                or not 200 <= response["status"] <= 599):
            raise ValueError("invalid or duplicate background Discord channel read")
        responses[guild] = response
    for row in data["services"][service]:
        if (row["method"] == "POST" and row["path"] == "/v1/reads"
                and ("request_json" not in row
                     or _discord_guild(row["request_json"]) in responses)):
            raise ValueError("background Discord read collides with sequential playback")
    return service, json.loads(json.dumps(responses, allow_nan=False))


class Playback:
    """One open of a snapshot: fresh consumption state and the environment
    unrecorded requests report to.

    Positions live on the playback, never the snapshot, so two openings of
    one `RecordedServices` consume independently. `bind` attaches the one
    `IsolatedServices` the served apps run inside — the caller composes it,
    which is what lets a journey merge the recording with its own real
    routes.
    """

    def __init__(
        self, data: dict[str, Any], *, optional_services: frozenset[str] = frozenset(),
        background_discord: tuple[str | None, dict[str, Any]] | None = None,
    ) -> None:
        #: The declared config-path -> service-name map.
        self.bindings: dict[str, str] = dict(data["bindings"])
        self._services = data["services"]
        self._optional_services = optional_services
        self._background_discord = background_discord
        self._positions = dict.fromkeys(self._services, 0)
        self._reads: dict[str, dict[str, Any]] = {name: {} for name in self._services}
        self._locks = {name: asyncio.Lock() for name in self._services}
        self.environment: IsolatedServices | None = None

    def bind(self, environment: IsolatedServices) -> None:
        """The one environment this playback reports unrecorded requests to."""
        if self.environment is not None:
            raise RuntimeError("a recorded playback serves one environment")
        self.environment = environment

    def applications(
        self, *, before: Callable[[str, web.Application], None] | None = None
    ) -> dict[str, web.Application]:
        """The recorded catch-all app per declared service, keyed by service name.

        `before(service, app)` registers the caller's own routes ahead of the
        recording's, so a composed world keeps its real seams — a recorded
        row can never stand in for them.
        """
        apps = {}
        for name, rows in self._services.items():
            app = web.Application()
            if before is not None:
                before(name, app)
            app.router.add_route("*", "/{tail:.*}", self._handler(name, rows))
            apps[name] = app
        return apps

    def check(self) -> None:
        """Require full consumption of required services and any optional service used."""
        if any(
            self._positions[name] != len(rows)
            and (name not in self._optional_services or self._positions[name] != 0)
            for name, rows in self._services.items()
        ):
            raise EvidenceError("external_response_not_consumed")

    def _handler(self, name: str, rows: list[Any]) -> Any:
        async def handle(request: web.Request) -> web.StreamResponse:
            if (self._background_discord and name == self._background_discord[0]
                    and request.method == "POST" and request.raw_path == "/v1/reads"):
                try:
                    guild = _discord_guild(await request.json())
                except (ValueError, UnicodeDecodeError):
                    guild = None
                if guild is not None and (response := self._background_discord[1].get(guild)):
                    return web.json_response(response["json"], status=response["status"])
            async with self._locks[name]:
                position = self._positions[name]
                row = rows[position] if position < len(rows) else None
                matches = (
                    row is not None
                    and request.method == row["method"]
                    and request.raw_path == row["path"]
                    and (not row.get("repeatable") or not request.can_read_body)
                )
                if row is not None and matches and "request_json" in row:
                    try:
                        matches = _same(await request.json(), row["request_json"])
                    except (ValueError, UnicodeDecodeError):
                        matches = False
                if row is not None and matches:
                    self._positions[name] += 1
                    if row["method"] == "GET" and row.get("repeatable"):
                        self._reads[name][row["path"]] = row
                elif request.method == "GET" and not request.can_read_body and (
                    replay := self._reads[name].get(request.raw_path)
                ) is not None:
                    row = replay
                else:
                    environment = self.environment
                    if environment is None:
                        raise RuntimeError("recorded playback unbound")
                    environment.unrecorded_response(
                        service=name, position=str(position), method=request.method,
                        path=request.raw_path,
                        expected_method=row["method"] if row else "exhausted",
                        expected_path=row["path"] if row else "exhausted")
                    return web.Response(status=500, text="external response not recorded")
                response = row["response"]
                headers = response.get("headers", {})
                if "chunks" in response:
                    stream = web.StreamResponse(status=response["status"], headers=headers)
                    stream.headers.setdefault("Content-Type", "text/event-stream")
                    await stream.prepare(request)
                    for chunk in response["chunks"]:
                        await stream.write(
                            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode()
                        )
                    await stream.write(b"data: [DONE]\n\n")
                    return stream
                if "json" in response:
                    result = web.Response(
                        # Discovery responses can refer to this playback's owned listener.
                        body=json.dumps(response["json"]).replace(
                            "${service_origin}", str(request.url.origin())
                        ).encode(),
                        status=response["status"], headers=headers,
                    )
                    result.headers.setdefault("Content-Type", "application/json")
                    return result
                return web.Response(status=response["status"], headers=headers)
        return handle


class RecordedServices:
    """A validated snapshot, reusable for independent isolated cases."""

    def __init__(
        self, data: dict[str, Any], *, model_gateway: ModelGateway | None = None,
        browser_fixture: bool = False,
        optional_services: frozenset[str] = frozenset(),
        background_discord_reads: Any = None,
    ) -> None:
        _validate(data)
        if model_gateway is not None and any(
            path == "llm.base_url" or (path.startswith("providers.") and path.endswith(".base_url"))
            for path in data["bindings"]
        ):
            raise ValueError("recorded model bindings cannot shadow a declared model gateway")
        if browser_fixture and BROWSER_BINDING in data["bindings"]:
            raise ValueError("recorded computer binding conflicts with routing browser fixture")
        for service in optional_services:
            bindings = [path for path, name in data["bindings"].items() if name == service]
            if (model_gateway is None or len(bindings) != 1
                    or bindings[0] not in _OPTIONAL_WEB_BINDINGS
                    or not data["services"].get(service)):
                raise ValueError("optional service requires one recorded web provider binding")
        self._optional_services = optional_services
        self._background_discord = _background_discord(
            data, [] if background_discord_reads is None else background_discord_reads,
            routing=model_gateway is not None,
        )
        self._data = json.loads(json.dumps(data, allow_nan=False))
        self.model_gateway = model_gateway
        self.browser_fixture = browser_fixture

    @property
    def bindings(self) -> dict[str, str]:
        """The declared config-path -> service-name map."""
        return dict(self._data["bindings"])

    @classmethod
    def load(cls, path: str | Path) -> RecordedServices:
        data = json.loads(
            Path(path).read_text(),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
        return cls(data)

    def playback(self) -> Playback:
        """One open of this snapshot: its apps, bindings and consumption check."""
        return Playback(self._data, optional_services=self._optional_services,
                        background_discord=self._background_discord)

    @asynccontextmanager
    async def open(self, database_url: str, redis: bool = False) -> AsyncIterator[IsolatedServices]:
        playback = self.playback()
        applications = playback.applications()
        bindings = {binding: applications[name] for binding, name in playback.bindings.items()}
        with ExitStack() as resources:
            fixture = None
            if self.browser_fixture:
                registry = Path(
                    resources.enter_context(TemporaryDirectory(prefix="nora-routing-browser-"))
                )
                fixture = RoutingBrowserFixture(registry)
                bindings[BROWSER_BINDING] = fixture.application()
            environment = IsolatedServices(
                database_url=database_url,
                redis=redis,
                model_gateway=self.model_gateway,
                applications=bindings,
                routing_browser=fixture,
            )
            playback.bind(environment)
            async with environment:
                yield environment
                environment.check()
                playback.check()
