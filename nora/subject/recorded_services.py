"""Recorded HTTP responses at the isolated external-service boundary."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError, current


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
                item, {"method", "path", "response"}, {"method", "path", "request_json", "response"}
            )
            method, path = row["method"], row["path"]
            if not isinstance(method, str) or not re.fullmatch(r"[A-Z]+", method):
                raise ValueError("invalid recorded method")
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                raise ValueError("invalid recorded path")
            parts = urlsplit(path)
            if parts.scheme or parts.netloc or parts.fragment or any(c.isspace() for c in path):
                raise ValueError("invalid recorded path")
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


class RecordedServices:
    """A validated snapshot, reusable for independent isolated cases."""

    def __init__(self, data: dict[str, Any]) -> None:
        _validate(data)
        self._data = json.loads(json.dumps(data, allow_nan=False))

    @classmethod
    def load(cls, path: str | Path) -> RecordedServices:
        data = json.loads(
            Path(path).read_text(),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
        return cls(data)

    @asynccontextmanager
    async def open(self, database_url: str, redis: bool = False) -> AsyncIterator[IsolatedServices]:
        positions = dict.fromkeys(self._data["services"], 0)
        locks = {name: asyncio.Lock() for name in positions}
        applications = {}
        environment: IsolatedServices
        for name, rows in self._data["services"].items():
            app = web.Application()

            async def handle(
                request: web.Request, name: str = name, rows: list[Any] = rows
            ) -> web.StreamResponse:
                async with locks[name]:
                    position = positions[name]
                    row = rows[position] if position < len(rows) else None
                    matches = (
                        row is not None
                        and request.method == row["method"]
                        and request.raw_path == row["path"]
                    )
                    if row is not None and matches and "request_json" in row:
                        try:
                            matches = _same(await request.json(), row["request_json"])
                        except (ValueError, UnicodeDecodeError):
                            matches = False
                    if row is None or not matches:
                        diagnostic = {
                            "operation": "unrecorded_response",
                            "cause": "external_response_not_recorded",
                            "service": name, "position": str(position),
                            "method": request.method, "path": request.raw_path,
                            "expected_method": row["method"] if row else "exhausted",
                            "expected_path": row["path"] if row else "exhausted",
                        }
                        environment.denied.append(diagnostic)
                        if evidence := current():
                            evidence.record("external_service_mismatch", **diagnostic)
                        return web.Response(status=500, text="external response not recorded")
                    positions[name] += 1
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
                            body=json.dumps(response["json"]).encode(),
                            status=response["status"], headers=headers,
                        )
                        result.headers.setdefault("Content-Type", "application/json")
                        return result
                    return web.Response(status=response["status"], headers=headers)
            app.router.add_route("*", "/{tail:.*}", handle)
            applications[name] = app
        environment = IsolatedServices(
            database_url=database_url,
            redis=redis,
            applications={
                binding: applications[name] for binding, name in self._data["bindings"].items()
            },
        )
        async with environment:
            yield environment
            environment.check()
            if any(positions[name] != len(rows) for name, rows in self._data["services"].items()):
                raise EvidenceError("external_response_not_consumed")
