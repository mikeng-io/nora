#!/usr/bin/env python3
"""Fixed shared-proxy relay and isolated downloader launcher; see outbound-fetches.md."""

from __future__ import annotations

import fcntl
import os
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


def relay(left: socket.socket, right: socket.socket) -> None:
    def copy(source: socket.socket, destination: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                destination.sendall(data)
        except OSError:
            pass
        finally:
            with suppress(OSError):
                destination.shutdown(socket.SHUT_WR)

    # Independent pumps keep a blocked writer from stalling reverse traffic.
    forward = threading.Thread(target=copy, args=(left, right), daemon=True)
    forward.start()
    copy(right, left)
    forward.join()


class FixedRelay(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        # One server-owned endpoint; never interpret client-selected destinations here.
        with (
            suppress(OSError),
            socket.create_connection(("http-egress", 4750), timeout=10) as remote,
        ):
            remote.settimeout(None)
            relay(self.request, remote)


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


def runtime_environment() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
        if key in os.environ
    }


def inside(proxy_path: str, command: list[str]) -> int:
    # Only loopback exists in this fresh network namespace.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        fcntl.ioctl(control, 0x8914, struct.pack("16sH22x", b"lo", 0x1 | 0x8 | 0x40))
    with RelayServer(("127.0.0.1", 0), LocalRelay) as server:
        server.proxy_path = proxy_path
        threading.Thread(target=server.serve_forever, daemon=True).start()
        proxy = f"http://127.0.0.1:{server.server_address[1]}"
        env = runtime_environment()
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
        with ProxyServer(proxy_path, FixedRelay) as server:
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
                env=runtime_environment(),
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
