#!/usr/bin/env python3
"""Public-only proxy and isolated downloader launcher; see outbound-fetches.md."""

from __future__ import annotations

import fcntl
import http.server
import ipaddress
import os
import select
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_reserved


def connect_public(host: str, port: int) -> socket.socket:
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers or any(not public_address(a[4][0]) for a in answers):
        raise OSError("non-public destination")
    for family, kind, proto, _, address in answers:
        connection = socket.socket(family, kind, proto)
        connection.settimeout(30)
        try:
            connection.connect(address)
            return connection
        except OSError:
            connection.close()
    raise OSError("destination unavailable")


def relay(left: socket.socket, right: socket.socket) -> None:
    while True:
        ready, _, _ = select.select([left, right], [], [], 60)
        if not ready:
            return
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            (right if source is left else left).sendall(data)


class PublicProxy(http.server.BaseHTTPRequestHandler):
    # Unbuffered input leaves CONNECT's following TLS bytes on the socket.
    rbufsize = 0

    def log_message(self, *_args: object) -> None:
        pass

    def handle_one_request(self) -> None:
        self.connection.settimeout(30)
        try:
            super().handle_one_request()
        except (OSError, ValueError):
            self.close_connection = True

    def do_CONNECT(self) -> None:
        self.forward(tunnel=True)

    def do_GET(self) -> None:
        self.forward(tunnel=False)

    do_HEAD = do_POST = do_PUT = do_DELETE = do_OPTIONS = do_PATCH = do_GET  # noqa: N815

    def forward(self, *, tunnel: bool) -> None:
        self.close_connection = True
        try:
            target = urlsplit("//" + self.path if tunnel else self.path)
            if not tunnel and target.scheme != "http":
                raise ValueError("unsupported proxy scheme")
            if not target.hostname or target.username or target.password:
                raise ValueError("invalid proxy target")
            port = target.port or (443 if tunnel else 80)
            with connect_public(target.hostname, port) as remote:
                if tunnel:
                    self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    path = target.path or "/"
                    if target.query:
                        path += "?" + target.query
                    lines = [f"{self.command} {path} HTTP/1.1"]
                    lines += [
                        f"{key}: {value}"
                        for key, value in self.headers.items()
                        if key.lower()
                        not in ("connection", "proxy-connection", "proxy-authorization", "host")
                    ]
                    lines += [f"Host: {target.netloc}", "Connection: close", "", ""]
                    remote.sendall("\r\n".join(lines).encode("latin-1"))
                relay(self.connection, remote)
        except (OSError, ValueError):
            # Do not expose destination details in job logs.
            self.send_error(502, "public destination unavailable")


class ProxyServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class LocalRelay(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as remote:
            remote.connect(self.server.proxy_path)
            with suppress(OSError):
                relay(self.request, remote)


class RelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def inside(proxy_path: str, command: list[str]) -> int:
    # Only loopback exists in this fresh network namespace.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        fcntl.ioctl(control, 0x8914, struct.pack("16sH22x", b"lo", 0x1 | 0x8 | 0x40))
    with RelayServer(("127.0.0.1", 0), LocalRelay) as server:
        server.proxy_path = proxy_path
        threading.Thread(target=server.serve_forever, daemon=True).start()
        proxy = f"http://127.0.0.1:{server.server_address[1]}"
        env = os.environ.copy()
        for key in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            env[key] = proxy
        env["no_proxy"] = env["NO_PROXY"] = ""
        if Path(command[0]).name.startswith("yt-dlp"):
            command[1:1] = ["--ignore-config", "--proxy", proxy]
        # PID namespace init exiting also kills descendants (including ffmpeg).
        return subprocess.call(command, env=env)  # noqa: S603 -- fixed server-selected executable


def run(command: list[str]) -> int:
    if sys.platform != "linux":
        raise RuntimeError("public downloader requires Linux network namespaces")
    with tempfile.TemporaryDirectory(prefix="nora-egress-") as directory:
        proxy_path = str(Path(directory) / "proxy.sock")
        with ProxyServer(proxy_path, PublicProxy) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            child = subprocess.Popen(  # noqa: S603 -- namespace wrapper and argument array
                [
                    "/usr/bin/setpriv",
                    "--pdeathsig",
                    "KILL",
                    "/usr/bin/unshare",
                    "--user",
                    "--map-root-user",
                    "--net",
                    "--pid",
                    "--fork",
                    "--kill-child",
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--inside",
                    proxy_path,
                    *command,
                ],
                start_new_session=True,
            )

            def stop(_signum: int, _frame: object) -> None:
                child.kill()

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                return child.wait()
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
                server.shutdown()


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--inside":
            result = inside(sys.argv[2], sys.argv[3:])
        else:
            result = run(sys.argv[1:])
    except (OSError, RuntimeError) as error:
        print(f"public downloader unavailable: {type(error).__name__}", file=sys.stderr)
        result = 125
    sys.exit(result)
