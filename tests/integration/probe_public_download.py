"""Offline Linux namespace regression. Run in the disposable fixture documented in security docs."""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

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

        class DNS(socketserver.BaseRequestHandler):
            def handle(self):
                data, sock = self.request
                offset, labels = 12, []
                while data[offset]:
                    size = data[offset]
                    labels.append(data[offset + 1 : offset + 1 + size].decode())
                    offset += size + 1
                name = ".".join(labels)
                end = offset + 5
                kind = int.from_bytes(data[offset + 1 : offset + 3], "big")
                addresses = []
                if kind == 1:
                    dns_calls.append(name)
                    if name == "mixed.invalid":
                        addresses = ["127.0.0.1", PUBLIC]
                    elif name == "rebind.invalid":
                        addresses = [PUBLIC if dns_calls.count(name) == 1 else "10.77.0.2"]
                answer = data[:2] + struct.pack("!HHHHH", 0x8180, 1, len(addresses), 0, 0)
                answer += data[12:end]
                for ip in addresses:
                    answer += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, 4) + socket.inet_aton(ip)
                sock.sendto(answer, self.client_address)

        dns = socketserver.ThreadingUDPServer(("0.0.0.0", 53), DNS)
        self.addCleanup(dns.server_close)
        self.addCleanup(dns.shutdown)
        threading.Thread(target=dns.serve_forever, daemon=True).start()
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
            for kind, name in [("hls", "playlist.m3u8"), ("dash", "manifest.mpd")]:
                subprocess.run(  # noqa: S603 -- generated media fixture
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(media),
                        "-c:a",
                        "aac",
                        "-f",
                        kind,
                        str(root / name),
                    ],
                    check=True,
                    capture_output=True,
                )

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
                    if self.path == "/socket":
                        self.send_response(101)
                        self.send_header("Upgrade", "websocket")
                        self.send_header("Connection", "Upgrade")
                        self.end_headers()
                        self.wfile.write(b"fixture-stream")
                        return
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
                    elif (root / self.path.lstrip("/")).is_file():
                        source = root / self.path.lstrip("/")
                        body = source.read_bytes()
                        kind = {
                            ".m3u8": "application/vnd.apple.mpegurl",
                            ".mpd": "application/dash+xml",
                        }.get(source.suffix, "application/octet-stream")
                    else:
                        body, kind = content, "audio/wav"
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            origin = http.server.ThreadingHTTPServer(("0.0.0.0", 80), Origin)
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

            secure = TLSOrigin(("0.0.0.0", 443), Origin)
            threading.Thread(target=secure.serve_forever, daemon=True).start()
            tls_port = secure.server_port

            binary = root / "yt-dlp"
            binary.write_text(f"#!{sys.executable}\nfrom yt_dlp import main\nmain()\n")
            binary.chmod(0o755)

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
                os.environ["SSL_CERT_FILE"] = str(cert)
                self.assertEqual(
                    proxy.run(download(f"http://public.invalid:{port}/control.wav")), 0
                )
                self.assertEqual((root / "out.wav").read_bytes(), content)
                self.assertIn("/control.wav", hits)
                hits.clear()
                self.assertEqual(
                    proxy.run(download("http://mixed.invalid/control.wav", "mixed")), 0
                )
                self.assertIn("/control.wav", hits)
                hits.clear()
                self.assertNotEqual(
                    proxy.run(download("http://rebind.invalid/control.wav", "first")), 0
                )
                self.assertIn("/control.wav", hits, "first public DNS answer never reached origin")
                hits.clear()
                self.assertNotEqual(
                    proxy.run(download("http://rebind.invalid/secret.wav", "second")), 0
                )
                self.assertEqual(hits, [], "changed DNS reached private origin")

                self.assertEqual(
                    proxy.run(download(f"https://public.invalid:{tls_port}/control.wav", "tls")),
                    0,
                )
                self.assertEqual((root / "outtls.wav").read_bytes(), content)
                self.assertTrue(accepted)
                self.assertIn("public.invalid", names)
                hits.clear()
                self.assertNotEqual(
                    proxy.run(download(f"https://wrong.invalid:{tls_port}/secret.wav", "wrong")),
                    0,
                )
                self.assertEqual(hits, [], "HTTPS hostname mismatch was accepted")
                before = len(accepted)
                self.assertNotEqual(
                    proxy.run(download(f"https://10.77.0.2:{tls_port}/secret.wav", "private-tls")),
                    0,
                )
                self.assertEqual(len(accepted), before, "private CONNECT reached TLS server")
                import urllib.request

                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    f"http://10.77.0.2:{port}/private-control"
                ) as response:
                    self.assertEqual(response.status, 200)
                self.assertIn("/private-control", hits)
                hits.clear()
                for target in [
                    f"http://10.77.0.2:{port}/secret.wav",
                    f"http://127.0.0.1:{port}/secret.wav",
                    f"http://[::ffff:127.0.0.1]:{port}/secret.wav",
                    f"http://10.0.0.1:{port}/secret.wav",
                    f"http://169.254.169.254:{port}/secret.wav",
                    f"http://100.64.0.1:{port}/secret.wav",
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
                for name in ["playlist.m3u8", "manifest.mpd"]:
                    self.assertEqual(proxy.run(download(f"http://public.invalid/{name}", name)), 0)
                    self.assertIn("/" + name, hits)
                    self.assertTrue(any(path.endswith((".ts", ".m4s")) for path in hits))
                    hits.clear()
                # Ignoring/overriding proxy environment must still fail at the namespace.
                for direct in [
                    f'import socket; socket.create_connection(("{PUBLIC}", 80), timeout=1)',
                    (
                        "import socket; "
                        'socket.create_connection(("2606:4700:4700::1111", 443), timeout=1)'
                    ),
                    f'import os,urllib.request; os.environ["NO_PROXY"]=os.environ["no_proxy"]="*"; urllib.request.urlopen("http://{PUBLIC}/secret.wav",timeout=1)',
                ]:
                    self.assertNotEqual(proxy.run([sys.executable, "-c", direct]), 0)
                    self.assertEqual(hits, [])
                # Real Upgrade bytes over HTTP and a verified CONNECT tunnel.
                for secure_socket in [True, False]:
                    code = r"""
import os, socket, ssl, sys
from urllib.parse import urlsplit
p = urlsplit(os.environ["http_proxy"])
s = socket.create_connection((p.hostname, p.port), timeout=3)
if sys.argv[1] == "True":
    s.sendall(b"CONNECT public.invalid:443 HTTP/1.1\r\nHost: public.invalid:443\r\n\r\n")
    response = b""
    while not response.endswith(b"\r\n\r\n"):
        response += s.recv(1)
    assert b"200" in response
    s = ssl.create_default_context().wrap_socket(s, server_hostname="public.invalid")
    target = "/socket"
else:
    target = "http://public.invalid/socket"
s.sendall((f"GET {target} HTTP/1.1\r\nHost: public.invalid\r\n"
           "Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n").encode())
response = b""
while b"fixture-stream" not in response:
    data = s.recv(4096)
    assert data, response
    response += data
assert b"101" in response
s.close()
"""
                    if not secure_socket:
                        direct_code = code.replace(
                            "s = socket.create_connection((p.hostname, p.port), timeout=3)",
                            f"s = socket.create_connection(('{PUBLIC}', 80), timeout=3)",
                        )
                        control = subprocess.run(  # noqa: S603 -- owned synthetic origin
                            [sys.executable, "-c", direct_code, "False"],
                            env={**os.environ, "http_proxy": "http://unused.invalid:80"},
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(control.returncode, 0, control.stderr)
                        self.assertIn("/socket", hits)
                        print("plain WS direct positive control passed", flush=True)
                        hits.clear()
                    self.assertEqual(proxy.run([sys.executable, "-c", code, str(secure_socket)]), 0)
                    print(f"gateway Upgrade secure={secure_socket} passed", flush=True)
                    self.assertIn("/socket", hits)
                    hits.clear()
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
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
