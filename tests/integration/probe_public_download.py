"""Offline Linux namespace regression. Run in the disposable fixture documented in security docs."""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "public_download", ROOT / "app/scripts/public-download.py"
)
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)
PUBLIC = "93.184.216.34"


class PublicDownloadBoundary(unittest.TestCase):
    def test_cancellation_kills_namespace_descendants(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "descendant"
            grandchild = (
                "from pathlib import Path; import time; "
                f"Path({str(marker)!r}).write_text(Path('/proc/self/status').read_text()); "
                "time.sleep(60)"
            )
            command = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
            )
            parent = subprocess.Popen(  # noqa: S603 -- local lifecycle fixture
                [
                    sys.executable,
                    str(ROOT / "app/scripts/public-download.py"),
                    sys.executable,
                    "-c",
                    command,
                ],
            )
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "namespace descendant did not start")
                pid = next(
                    line for line in marker.read_text().splitlines() if line.startswith("NSpid:")
                ).split()[1]
                status = Path(f"/proc/{pid}/status")
                self.assertTrue(status.exists(), "positive descendant control missing")
                parent.kill()
                parent.wait(timeout=5)

                def live():
                    try:
                        return "State:\tZ" not in status.read_text()
                    except FileNotFoundError:
                        return False

                deadline = time.monotonic() + 5
                while live() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(live(), "live downloader descendant survived cancellation")
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=5)

    def test_real_ytdlp_ffmpeg_and_direct_subprocess_boundary(self):
        hits = []
        dns_calls = []
        real_dns = socket.getaddrinfo
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "clip.wav"
            subprocess.run(  # noqa: S603 -- generated local fixture
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.1",
                    str(media),
                ],
                check=True,
            )
            content = media.read_bytes()
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run(  # noqa: S603 -- generated local test certificate
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=public.invalid",
                    "-addext",
                    "subjectAltName=DNS:public.invalid",
                ],
                check=True,
                capture_output=True,
            )
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
            accepted = []
            names = []
            tls.set_servername_callback(lambda sock, name, ctx: names.append(name))

            class Origin(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    hits.append(self.path)
                    if self.path == "/redirect":
                        self.send_response(302)
                        self.send_header(
                            "Location", f"http://127.0.0.1:{self.server.server_port}/secret.wav"
                        )
                        self.end_headers()
                        return
                    if self.path == "/secondary":
                        body = f'<html><title>secondary</title><video src="http://127.0.0.1:{self.server.server_port}/secret.wav"></video></html>'.encode()
                        kind = "text/html"
                    else:
                        body, kind = content, "audio/wav"
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            origin = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Origin)
            threading.Thread(target=origin.serve_forever, daemon=True).start()
            port = origin.server_port

            class TLSOrigin(http.server.ThreadingHTTPServer):
                def get_request(self):
                    sock, address = super().get_request()
                    accepted.append(address)
                    try:
                        return tls.wrap_socket(sock, server_side=True), address
                    except OSError:
                        sock.close()
                        raise

            secure = TLSOrigin(("0.0.0.0", 0), Origin)
            threading.Thread(target=secure.serve_forever, daemon=True).start()
            tls_port = secure.server_port

            binary = root / "yt-dlp"
            binary.write_text(f"#!{sys.executable}\nfrom yt_dlp import main\nmain()\n")
            binary.chmod(0o755)

            def resolve(host, requested_port, *args, **kwargs):
                if host.endswith(".invalid"):
                    dns_calls.append(host)
                    if host == "mixed.invalid":
                        return [
                            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, requested_port))
                            for ip in (PUBLIC, "127.0.0.1")
                        ]
                    ip = (
                        "127.0.0.1"
                        if host == "rebind.invalid" and dns_calls.count(host) > 1
                        else PUBLIC
                    )
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, requested_port))]
                return real_dns(host, requested_port, *args, **kwargs)

            def download(url, suffix=""):
                return [
                    str(binary),
                    "--compat-options",
                    "no-certifi",
                    "--no-warnings",
                    "--no-playlist",
                    "--retries",
                    "0",
                    "--socket-timeout",
                    "3",
                    "-o",
                    str(root / ("out" + suffix + ".%(ext)s")),
                    url,
                ]

            try:
                with (
                    patch.object(socket, "getaddrinfo", resolve),
                    patch.dict(os.environ, {"SSL_CERT_FILE": str(cert)}),
                ):
                    self.assertEqual(
                        proxy.run(download(f"http://public.invalid:{port}/control.wav")), 0
                    )
                    self.assertEqual((root / "out.wav").read_bytes(), content)
                    self.assertIn("/control.wav", hits)
                    hits.clear()

                    self.assertEqual(
                        proxy.run(
                            download(f"https://public.invalid:{tls_port}/control.wav", "tls")
                        ),
                        0,
                    )
                    self.assertEqual((root / "outtls.wav").read_bytes(), content)
                    self.assertTrue(accepted)
                    self.assertIn("public.invalid", names)
                    hits.clear()
                    self.assertNotEqual(
                        proxy.run(
                            download(f"https://wrong.invalid:{tls_port}/secret.wav", "wrong")
                        ),
                        0,
                    )
                    self.assertEqual(hits, [], "HTTPS hostname mismatch was accepted")
                    before = len(accepted)
                    self.assertNotEqual(
                        proxy.run(
                            download(f"https://127.0.0.1:{tls_port}/secret.wav", "private-tls")
                        ),
                        0,
                    )
                    self.assertEqual(len(accepted), before, "private CONNECT reached TLS server")
                    # A submit-time public answer followed by a private proxy answer.
                    socket.getaddrinfo("rebind.invalid", port)
                    result = proxy.run(
                        download(f"http://rebind.invalid:{port}/secret.wav", "rebind")
                    )
                    self.assertEqual(hits, [], f"private server reached after rebinding: {hits}")
                    self.assertNotEqual(result, 0)
                    result = proxy.run(download(f"http://mixed.invalid:{port}/secret.wav", "mixed"))
                    self.assertEqual(hits, [], f"mixed DNS answers reached origin: {hits}")
                    self.assertNotEqual(result, 0)
                    for target in [
                        f"http://127.0.0.1:{port}/secret.wav",
                        f"http://[::ffff:127.0.0.1]:{port}/secret.wav",
                    ]:
                        self.assertNotEqual(proxy.run(download(target, "literal")), 0)
                        self.assertEqual(hits, [], f"private literal server reached: {hits}")
                    for path in ["/redirect", "/secondary"]:
                        self.assertNotEqual(
                            proxy.run(download(f"http://public.invalid:{port}{path}", "hop")), 0
                        )
                        self.assertNotIn("/secret.wav", hits, f"private secondary reached: {hits}")
                        self.assertIn(path, hits)
                        hits.clear()
                    self.assertEqual(
                        proxy.run(
                            [
                                "ffmpeg",
                                "-v",
                                "error",
                                "-i",
                                f"http://public.invalid:{port}/control.wav",
                                "-f",
                                "null",
                                "-",
                            ]
                        ),
                        0,
                    )
                    self.assertIn("/control.wav", hits)
                    hits.clear()
                    self.assertNotEqual(
                        proxy.run(
                            [
                                "ffmpeg",
                                "-v",
                                "error",
                                "-i",
                                f"http://127.0.0.1:{port}/secret.wav",
                                "-f",
                                "null",
                                "-",
                            ]
                        ),
                        0,
                    )
                    self.assertEqual(hits, [], f"ffmpeg private server reached: {hits}")
                    # A child ignoring every proxy variable still cannot reach the origin.
                    direct = (
                        f'import socket; socket.create_connection(("{PUBLIC}", {port}), timeout=1)'
                    )
                    self.assertNotEqual(proxy.run([sys.executable, "-c", direct]), 0)
                    self.assertEqual(hits, [])
            finally:
                origin.shutdown()
                origin.server_close()
                secure.shutdown()
                secure.server_close()
            print(
                json.dumps(
                    {
                        "public_ytdlp": "passed",
                        "ffmpeg": "passed",
                        "https_host_sni_certificate": "passed",
                        "private_and_secondary": "denied",
                        "direct_bypass": "denied",
                        "dns_calls": len(dns_calls),
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
