"""Case-owned wire services; production clients keep their normal protocols."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import platform
import shutil
import socket
import ssl
import subprocess
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import httpx
import httpx2
from aiohttp import web
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from nora.subject.model_gateway import (
    _ADMITTED_DISPATCH,
    ModelGateway,
    ResolvedGateway,
)

#: The declared model upstream reachable for an in-flight admitted dispatch
#: on this task — the only non-owned destination the process guard opens,
#: and only for the duration of one admitted request.
_admitted_egress: ContextVar[ResolvedGateway | None] = ContextVar(
    "admitted_egress", default=None)


class IsolatedServices:
    """Own HTTP listeners and optional Redis for one evaluation case.

    Applications implement external services, never Nora's control loop.
    Missing routes retain their protocol errors. Unbound network destinations
    are refused, including clients which use a hard-coded remote endpoint.
    A declared `model_gateway` is the one exception: dispatches naming the
    declared identities are admitted to it, request by request, through the
    installed run budget's bounded transport.
    The context encloses composition, execution and dependency teardown.
    """

    def __init__(self, *, database_url: str,
                 applications: Mapping[str, web.Application],
                 redis: bool = False,
                 model_gateway: ModelGateway | None = None) -> None:
        self.database_url = database_url
        self._applications = dict(applications)
        self._redis = redis
        self._gateway = model_gateway
        self._egress: ResolvedGateway | None = None
        self.urls: dict[str, str] = {}
        self.redis_url = ""
        self.denied: list[dict[str, str]] = []
        self._runners: list[web.AppRunner] = []
        self._process: asyncio.subprocess.Process | None = None
        self._directory: TemporaryDirectory[str] | None = None
        self._patches = contextlib.ExitStack()
        self._active = False
        self._addresses: set[tuple[str, int]] = set()
        self._database_alias: tuple[str, int] | None = None
        self._database_addresses: set[str] = set()
        self._unix: set[str] = set()
        self._subjects: list[Any] = []
        self._before_tasks: set[asyncio.Task[Any]] = set()
        #: The services plan that opened this environment, when the plan
        #: carries request-aware state a driver needs back (e.g. the journey
        #: world's counters). `None` for positional recordings.
        self.world: Any | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def guarded(self) -> bool:
        return self.active and getattr(socket.socket.connect,
                                       "_nora_eval_environment", None) is self

    def check(self) -> None:
        from nora.subject.eval_evidence import EvidenceError

        if not self.guarded:
            raise EvidenceError("external_dependency_not_isolated")
        if self.denied:
            raise EvidenceError("; ".join(sorted({row["cause"] for row in self.denied})))

    def unrecorded_response(self, *, service: str, method: str, path: str,
                            position: str = "unmatched", expected_method: str = "unmatched",
                            expected_path: str = "unmatched") -> None:
        from nora.subject.eval_evidence import current

        diagnostic = {"operation": "unrecorded_response", "cause": "external_response_not_recorded",
                      "service": service, "position": position, "method": method, "path": path,
                      "expected_method": expected_method, "expected_path": expected_path}
        self.denied.append(diagnostic)
        if evidence := current():
            evidence.record("external_service_mismatch", **diagnostic)

    def register_subject(self, subject: Any) -> None:
        self._subjects.append(subject)

    async def close_subject(self, subject: Any) -> None:
        if not any(owned is subject for owned in self._subjects):
            return
        self._subjects = [owned for owned in self._subjects if owned is not subject]
        from nora.app.lifecycle import close_dependencies

        await close_dependencies(subject.deps)

    def owns(self, url: str) -> bool:
        origin = urlsplit(url)
        return self.active and bool(url) and (
            url == self.redis_url or any(
                origin.scheme == "http" and origin.netloc == urlsplit(owned).netloc
                for owned in self.urls.values()))

    def bind_config(self, cfg: Any) -> Any:
        """Change resource addresses, retaining behavior and API base paths."""
        from nora.subject.obtain import _with

        self.check()
        for path, url in self.urls.items():
            node = cfg
            parts = path.split(".")
            try:
                for part in parts[:-1]:
                    if isinstance(node, dict):
                        node = node[part]
                    elif isinstance(node, BaseModel) and part in type(node).model_fields:
                        node = getattr(node, part)
                    else:
                        raise ValueError
                field = parts[-1]
                if (not isinstance(node, BaseModel) or field not in type(node).model_fields
                        or not (field == "url" or field.endswith("_url") or field == "endpoint")
                        or field.startswith("public_")):
                    raise ValueError
                node = getattr(node, field)
                original = urlsplit(node)
                if original.scheme not in ("http", "https") or not original.hostname:
                    raise ValueError
            except (KeyError, AttributeError, TypeError, ValueError):
                raise ValueError(
                    f"{path}: an isolated binding must name an HTTP endpoint"
                ) from None
            replacement = urlsplit(url)
            cfg = _with(cfg, path, urlunsplit((replacement.scheme, replacement.netloc,
                                              original.path, original.query, "")))
        model_urls = [cfg.llm.base_url, *(provider.base_url for provider in cfg.providers.values())]
        if any(url and not self.owns(url) and not self._declared_endpoint(url)
               for url in model_urls):
            raise ValueError("model dispatch requires a case-owned service boundary")
        variable = f"NORA_SUBJECT_DATABASE_URL_{uuid.uuid4().hex}"
        cfg = _with(cfg, "store.database_url_env", variable)
        cfg._credentials = {**cfg.credentials, variable: self.database_url}
        if self.redis_url:
            cfg._credentials = {**cfg.credentials, cfg.bridges.redis_url_env: self.redis_url}
        return cfg

    def _declared_endpoint(self, url: str) -> bool:
        """Whether `url` IS the declared model gateway's upstream origin."""
        egress = self._egress
        if egress is None:
            return False
        parts = urlsplit(url)
        return (parts.scheme, (parts.hostname or "").lower(),
                parts.port or (443 if parts.scheme == "https" else 80)) == (
            egress.scheme, egress.host, egress.port)

    def _deny_dispatch(self, cause: str, request: Any) -> None:
        """Record the refusal cause without copying request credentials or content."""
        self.denied.append({"operation": "http", "cause": cause})
        if cause != "external_dependency_not_isolated":
            from nora.subject.eval_evidence import current
            if evidence := current():
                evidence.record(
                    "external_service_mismatch", service="model_gateway",
                    position="boundary", method=request.method,
                    path=str(request.url.path),
                    expected_method="POST",
                    expected_path=self._egress.route if self._egress else "",
                    cause=cause)
        raise PermissionError(errno.EACCES, "evaluation dependency is not isolated")

    async def _model_refusal_async(self, client: Any, request: Any) -> str | None:
        if self._egress is None:
            return "external_dependency_not_isolated"
        try:
            raw = request.content
        except (httpx.StreamError, httpx2.StreamError):
            try:
                raw = await request.aread()
            except (httpx.StreamError, httpx2.StreamError):
                raw = None
        return self._egress.refusal(client, request, raw)

    def _model_refusal(self, client: Any, request: Any) -> str | None:
        if self._egress is None:
            return "external_dependency_not_isolated"
        try:
            raw = request.content
        except (httpx.StreamError, httpx2.StreamError):
            try:
                raw = request.read()
            except (httpx.StreamError, httpx2.StreamError):
                raw = None
        return self._egress.refusal(client, request, raw)

    async def __aenter__(self) -> IsolatedServices:
        if self._active or self._runners:
            raise RuntimeError("an evaluation environment cannot be re-entered")
        if getattr(socket.socket.connect, "_nora_eval_guard", False):
            raise RuntimeError("evaluation cases must not share a network context")
        try:
            # OpenAI's SDK lazily reads platform.platform() for its user-agent.
            # macOS may run uname/file for that read; resolve host metadata
            # before the effect boundary, without exempting later subprocesses.
            platform.platform()
            bound: dict[int, str] = {}
            for key, application in self._applications.items():
                if id(application) in bound:
                    self.urls[key] = bound[id(application)]
                    continue
                application.middlewares.append(self._coverage(key))
                runner = web.AppRunner(application, access_log=None)
                self._runners.append(runner)
                await runner.setup()
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    listener.bind(("127.0.0.1", 0))
                    listener.setblocking(False)
                    port = listener.getsockname()[1]
                    await web.SockSite(runner, listener).start()
                except BaseException:
                    listener.close()
                    raise
                self.urls[key] = f"http://127.0.0.1:{port}"
                bound[id(application)] = self.urls[key]
                self._addresses.add(("127.0.0.1", port))
            if self._redis:
                await self._start_redis()
            database = make_url(self.database_url)
            if database.host:
                if database.host.startswith("/"):
                    self._unix.add(str(Path(database.host) /
                                       f".s.PGSQL.{database.port or 5432}"))
                else:
                    port = database.port or 5432
                    self._database_alias = (database.host, port)
                    for item in socket.getaddrinfo(database.host, port,
                                                   type=socket.SOCK_STREAM):
                        address = str(item[4][0])
                        self._database_addresses.add(address)
                        self._addresses.add((address, port))
            if self._gateway is not None:
                # Real resolution, before the guard installs: the declared
                # upstream's addresses are pinned for the case's lifetime.
                self._egress = self._gateway.resolve()
            self._guard_network()
            self._before_tasks = asyncio.all_tasks()
            self._active = True
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    def _coverage(self, service: str) -> Any:
        @web.middleware
        async def coverage(request: web.Request, handler: Any) -> web.StreamResponse:
            if request.match_info.http_exception is not None:
                self.unrecorded_response(service=service, method=request.method,
                                         path=request.raw_path)
            return await handler(request)

        return coverage

    async def _start_redis(self) -> None:
        executable = shutil.which("redis-server")
        if executable is None:
            raise RuntimeError("isolated bridge execution requires redis-server")
        self._directory = TemporaryDirectory(prefix="nora-eval-redis-", dir="/tmp")
        path = str(Path(self._directory.name) / "redis.sock")
        self._unix.add(path)
        self.redis_url = f"unix://{path}"
        self._process = await asyncio.create_subprocess_exec(
            executable, "--port", "0", "--unixsocket", path,
            "--unixsocketperm", "700", "--save", "", "--appendonly", "no",
            "--dir", self._directory.name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        if self._process.stdout is None:
            raise RuntimeError("isolated Redis has no readiness stream")
        async with asyncio.timeout(5):
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    raise RuntimeError("isolated Redis failed before readiness")
                if b"ready to accept connections" in line.lower():
                    break

    def _guard_network(self) -> None:
        # ponytail: one case per process, as SDK max_workers=1 requires;
        # parallel case execution needs separate worker processes.
        if getattr(socket.socket.connect, "_nora_eval_guard", False):
            raise RuntimeError("evaluation cases must not share a network context")

        def allowed(address: Any) -> bool:
            if isinstance(address, tuple) and len(address) >= 2:
                if (str(address[0]), int(address[1])) in self._addresses:
                    return True
                egress = _admitted_egress.get()
                return (egress is not None
                        and (str(address[0]), int(address[1])) in egress.addresses)
            return isinstance(address, str) and address in self._unix

        def refuse(operation: str,
                   cause: str = "external_dependency_not_isolated") -> None:
            self.denied.append({"operation": operation, "cause": cause})
            raise PermissionError(errno.EACCES, "evaluation dependency is not isolated")

        original_lookup = socket.getaddrinfo

        def lookup(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            if self._database_alias == (host, port):
                result = []
                for address in sorted(self._database_addresses):
                    try:
                        result.extend(original_lookup(address, port, *args, **kwargs))
                    except socket.gaierror:
                        continue
                if not result:
                    raise socket.gaierror(socket.EAI_NONAME, "no owned address for database")
                return result
            egress = _admitted_egress.get()
            if egress is not None and (host, port) == (egress.host, egress.port):
                # The declared upstream resolves only inside an admitted
                # dispatch, and only ever to the addresses pinned at open.
                result = []
                for address, pinned in sorted(egress.addresses):
                    try:
                        result.extend(original_lookup(address, pinned, *args, **kwargs))
                    except socket.gaierror:
                        continue
                if not result:
                    raise socket.gaierror(socket.EAI_NONAME,
                                          "no owned address for model gateway")
                return result
            addresses = {h for h, _ in self._addresses}
            if host not in addresses and host not in (None, "localhost"):
                refuse("resolve")
            result = original_lookup(host, port, *args, **kwargs)
            if any(not allowed(item[4]) for item in result):
                refuse("resolve")
            return result

        self._patches.enter_context(patch.object(socket, "getaddrinfo", lookup))

        # Loopback services are direct even on developer hosts with a proxy.
        self._patches.enter_context(patch.dict(os.environ, {
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
        }))
        self._guard_requests()

        def subprocess_refused(*args: Any, **kwargs: Any) -> Any:
            refuse("subprocess")

        # SSH/browser/codec helpers otherwise escape the Python socket boundary.
        self._patches.enter_context(patch.object(subprocess.Popen, "__init__", subprocess_refused))
        for name in ("connect", "connect_ex"):
            original = getattr(socket.socket, name)

            def connect(sock: socket.socket, address: Any, *args: Any,
                        _original: Any = original, **kwargs: Any) -> Any:
                if not allowed(address):
                    refuse("connect")
                return _original(sock, address, *args, **kwargs)

            connect._nora_eval_guard = True  # type: ignore[attr-defined]
            connect._nora_eval_environment = self  # type: ignore[attr-defined]
            self._patches.enter_context(patch.object(socket.socket, name, connect))

        def check_connected(sock: socket.socket) -> None:
            peer, local = sock.getpeername(), sock.getsockname()
            # Anonymous socketpairs are asyncio's wake-up pipe, not egress.
            if sock.family == socket.AF_UNIX and peer == local == "":
                return
            # A socket bound to the declared upstream's address is the in-process
            # listener itself answering; its clients still had to connect under
            # an admitted dispatch, so replies flow without a standing exemption.
            egress = self._egress
            serving = (egress is not None and isinstance(local, tuple)
                       and len(local) >= 2
                       and (str(local[0]), int(local[1])) in egress.addresses)
            if not allowed(peer) and not allowed(local) and not serving:
                refuse("send")

        for cls, names in ((socket.socket, ("send", "sendall")),
                           (ssl.SSLSocket, ("write",))):
            for name in names:
                original = getattr(cls, name)

                def send(sock: socket.socket, *args: Any,
                         _original: Any = original, **kwargs: Any) -> Any:
                    check_connected(sock)
                    return _original(sock, *args, **kwargs)

                self._patches.enter_context(patch.object(cls, name, send))

        original_sendto = socket.socket.sendto

        def sendto(sock: socket.socket, data: Any, *args: Any) -> Any:
            if not args or not allowed(args[-1]):
                refuse("sendto")
            return original_sendto(sock, data, *args)

        self._patches.enter_context(patch.object(socket.socket, "sendto", sendto))

    def _guard_requests(self) -> None:
        # HTTPX2 may use a native transport; guard each dispatch, including
        # redirects, before either Python or native transport can send bytes.
        for module in (httpx, httpx2):
            original_async = module.AsyncClient._send_single_request
            original_sync = module.Client._send_single_request

            async def request_async(client: Any, request: Any,
                                    _original: Any = original_async) -> Any:
                if self.owns(str(request.url)):
                    return await _original(client, request)
                cause = await self._model_refusal_async(client, request)
                if cause is None:
                    request.extensions[_ADMITTED_DISPATCH] = True
                    token = _admitted_egress.set(self._egress)
                    try:
                        return await _original(client, request)
                    finally:
                        _admitted_egress.reset(token)
                self._deny_dispatch(cause, request)

            def request_sync(client: Any, request: Any,
                             _original: Any = original_sync) -> Any:
                if self.owns(str(request.url)):
                    return _original(client, request)
                cause = self._model_refusal(client, request)
                if cause is None:
                    request.extensions[_ADMITTED_DISPATCH] = True
                    token = _admitted_egress.set(self._egress)
                    try:
                        return _original(client, request)
                    finally:
                        _admitted_egress.reset(token)
                self._deny_dispatch(cause, request)

            self._patches.enter_context(patch.object(
                module.AsyncClient, "_send_single_request", request_async))
            self._patches.enter_context(patch.object(
                module.Client, "_send_single_request", request_sync))

    async def __aexit__(self, exc_type: type[BaseException] | None,
                        exc: BaseException | None,
                        tb: TracebackType | None) -> None:
        cleanup = asyncio.create_task(self._close())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _close(self) -> None:
        errors: list[BaseException] = []
        try:
            for subject in tuple(self._subjects):
                try:
                    await self.close_subject(subject)
                except BaseException as error:  # noqa: BLE001 -- finish cleanup, then re-raise all causes
                    errors.append(error)
            if self._before_tasks:
                pending = asyncio.all_tasks() - self._before_tasks - {asyncio.current_task()}
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            for runner in reversed(self._runners):
                try:
                    await runner.cleanup()
                except BaseException as error:  # noqa: BLE001 -- finish cleanup, then re-raise all causes
                    errors.append(error)
        finally:
            try:
                if self._process is not None and self._process.returncode is None:
                    self._process.terminate()
                    await self._process.wait()
            finally:
                self._active = False
                self._patches.close()
                if self._directory is not None:
                    self._directory.cleanup()
        if errors:
            raise BaseExceptionGroup("evaluation dependency cleanup failed", errors)

