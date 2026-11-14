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
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import httpx
import httpx2
from aiohttp import web
from sqlalchemy.engine import make_url


class IsolatedServices:
    """Own HTTP listeners and optional Redis for one evaluation case.

    Applications implement external services, never Nora's control loop.
    Missing routes retain their protocol errors. Unbound network destinations
    are refused, including clients which use a hard-coded remote endpoint.
    The context encloses composition, execution and dependency teardown.
    """

    def __init__(self, *, database_url: str,
                 applications: Mapping[str, web.Application],
                 redis: bool = False) -> None:
        self.database_url = database_url
        self._applications = dict(applications)
        self._redis = redis
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

    def register_subject(self, subject: Any) -> None:
        self._subjects.append(subject)

    async def close_subject(self, subject: Any) -> None:
        if not any(owned is subject for owned in self._subjects):
            return
        self._subjects = [owned for owned in self._subjects if owned is not subject]
        from nora.app.runtime import close_dependencies

        try:
            await subject.deps.background.cancel_all()
        finally:
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
            if not path.endswith((".base_url", ".endpoint", ".bridge_url", ".refresh_url")):
                raise ValueError(f"{path}: an isolated binding must name an endpoint")
            node = cfg
            for part in path.split("."):
                node = node[part] if isinstance(node, dict) else getattr(node, part)
            original, replacement = urlsplit(node), urlsplit(url)
            cfg = _with(cfg, path, urlunsplit((replacement.scheme, replacement.netloc,
                                              original.path, original.query, "")))
        model_urls = [cfg.llm.base_url, *(provider.base_url for provider in cfg.providers.values())]
        if any(url and not self.owns(url) for url in model_urls):
            raise ValueError("model dispatch requires a case-owned service boundary")
        variable = f"NORA_SUBJECT_DATABASE_URL_{uuid.uuid4().hex}"
        cfg = _with(cfg, "store.database_url_env", variable)
        cfg._credentials = {**cfg.credentials, variable: self.database_url}
        if self.redis_url:
            cfg._credentials = {**cfg.credentials, cfg.bridges.redis_url_env: self.redis_url}
        return cfg

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
                self.denied.append({"operation": "unrecorded_response",
                                    "cause": "external_response_not_recorded",
                                    "service": service, "method": request.method})
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
                return (str(address[0]), int(address[1])) in self._addresses
            return isinstance(address, str) and address in self._unix

        def refuse(operation: str) -> None:
            self.denied.append({"operation": operation,
                                "cause": "external_dependency_not_isolated"})
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
        # HTTPX2 may use a native transport; guard each dispatch, including
        # redirects, before either Python or native transport can send bytes.
        for module in (httpx, httpx2):
            original_async = module.AsyncClient._send_single_request
            original_sync = module.Client._send_single_request

            async def request_async(client: Any, request: Any,
                                    _original: Any = original_async) -> Any:
                if not self.owns(str(request.url)):
                    refuse("http")
                return await _original(client, request)

            def request_sync(client: Any, request: Any,
                             _original: Any = original_sync) -> Any:
                if not self.owns(str(request.url)):
                    refuse("http")
                return _original(client, request)

            self._patches.enter_context(patch.object(
                module.AsyncClient, "_send_single_request", request_async))
            self._patches.enter_context(patch.object(
                module.Client, "_send_single_request", request_sync))

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
            if not allowed(peer) and not allowed(local):
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

