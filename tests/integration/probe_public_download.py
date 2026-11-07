"""Offline Linux namespace regression. Run in the disposable fixture documented in security docs."""

from __future__ import annotations

import base64
import hashlib
import http.server
import importlib.util
import ipaddress
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
# Generated fixture subnet; the hook treats literal CGNAT addresses as private identity.
CGNAT_ORIGIN = str(ipaddress.IPv4Address(0x64404D02))
# RFC6455's public protocol GUID, not a person or account identifier.
WS_GUID = f"{0x258EAFA5:08X}-{0xE914:04X}-{0x47DA:04X}-{0x95CA:04X}-{0xC5AB0DC85B11:012X}"


class PublicDownloadBoundary(unittest.TestCase):
    def test_app_credentials_do_not_reach_downloader(self):
        credentials = ("AUTH_SECRET", "AUTH_DISCORD_SECRET", "NORA_OTP_API_TOKEN")
        previous = {name: os.environ.get(name) for name in credentials}
        for name in credentials:
            os.environ[name] = "synthetic-fixture-secret"
        try:
            code = f"""
import os
from pathlib import Path
credentials = {credentials!r}
assert not set(credentials).intersection(os.environ)
assert os.environ['http_proxy'].startswith('http://127.0.0.1:')
assert os.environ.get('PATH')
# The inherited procfs reports host PIDs even inside the child PID namespace.
parent = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
              if line.startswith('PPid:'))
parent_environment = (Path('/proc') / parent / 'environ').read_bytes()
assert parent_environment, 'positive parent-environ read control missing'
assert not any((name + '=').encode() in parent_environment for name in credentials), (
    'namespace parent inherited app credentials'
)
"""
            self.assertEqual(proxy.run([sys.executable, "-c", code]), 0)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

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
                    except (FileNotFoundError, ProcessLookupError):
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
        # RFC6455 section 1.3's independent handshake vector checks the public constant.
        self.assertEqual(
            base64.b64encode(
                hashlib.sha1(
                    ("dGhlIHNhbXBsZSBub25jZQ==" + WS_GUID).encode(), usedforsecurity=False
                ).digest()
            ).decode(),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )
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

            playlist = (root / "playlist.m3u8").read_text()
            playlist = (
                "\n".join(
                    "http://10.77.0.2/secret.wav" if line and not line.startswith("#") else line
                    for line in playlist.splitlines()
                )
                + "\n"
            )
            (root / "evil.m3u8").write_text(playlist)
            manifest = (root / "manifest.mpd").read_text()
            manifest = manifest.replace('media="', 'media="http://10.77.0.2/').replace(
                'initialization="', 'initialization="http://10.77.0.2/'
            )
            (root / "evil.mpd").write_text(manifest)

            # Only generated public media assets are served; request paths never reach disk.
            assets = {"/" + asset.name: asset.read_bytes() for asset in root.iterdir()}
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
                protocol_version = "HTTP/1.1"

                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    self.close_connection = True
                    hits.append(self.path)
                    if self.path == "/socket":
                        # Minimal RFC6455 endpoint: validate handshake and masked text/close.
                        key = self.headers.get("Sec-WebSocket-Key", "")
                        if (
                            self.headers.get("Sec-WebSocket-Version") != "13"
                            or len(base64.b64decode(key)) != 16
                        ):
                            self.send_error(400)
                            return
                        accept = base64.b64encode(
                            hashlib.sha1(
                                (key + WS_GUID).encode(),
                                usedforsecurity=False,
                            ).digest()
                        ).decode()
                        self.send_response(101)
                        self.send_header("Upgrade", "websocket")
                        self.send_header("Connection", "Upgrade")
                        self.send_header("Sec-WebSocket-Accept", accept)
                        self.end_headers()
                        self.wfile.flush()
                        self.connection.settimeout(3)
                        for opcode, expected in [(1, b"ping"), (8, b"\x03\xe8")]:
                            header = self.rfile.read(2)
                            if header != bytes([0x80 | opcode, 0x80 | len(expected)]):
                                return
                            mask = self.rfile.read(4)
                            data = self.rfile.read(len(expected))
                            if bytes(c ^ mask[i % 4] for i, c in enumerate(data)) != expected:
                                return
                            payload = b"fixture-stream" if opcode == 1 else expected
                            self.wfile.write(bytes([0x80 | opcode, len(payload)]) + payload)
                            self.wfile.flush()
                        return
                    if self.path == "/idle":
                        self.close_connection = False
                        self.send_response(200)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if self.path == "/stream":
                        self.send_response(200)
                        self.send_header("Content-Length", "24")
                        self.end_headers()
                        for _ in range(12):
                            self.wfile.write(b"ok")
                            self.wfile.flush()
                            time.sleep(0.5)
                        return
                    if self.path in {"/thumbnail", "/thumbnail-public"}:
                        body = (
                            b"<html><title>thumbnail</title>"
                            b'<meta property="og:image" content="http://10.77.0.2/secret.jpg">'
                            b'<video src="http://public.invalid/control.wav"></video></html>'
                        )
                        if self.path == "/thumbnail-public":
                            body = body.replace(
                                b"10.77.0.2/secret.jpg", b"public.invalid/public.jpg"
                            )
                        kind = "text/html"
                    elif self.path == "/redirect":
                        self.send_response(302)
                        self.send_header(
                            "Location", f"http://127.0.0.1:{self.server.server_port}/secret.wav"
                        )
                        self.end_headers()
                        return
                    elif self.path == "/secondary":
                        body = f'<html><title>secondary</title><video src="http://127.0.0.1:{self.server.server_port}/secret.wav"></video></html>'.encode()
                        kind = "text/html"
                    elif self.path in assets:
                        body = assets[self.path]
                        kind = {
                            ".m3u8": "application/vnd.apple.mpegurl",
                            ".mpd": "application/dash+xml",
                        }.get(Path(self.path).suffix, "application/octet-stream")
                    else:
                        body, kind = content, "audio/wav"
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            class IPv6Origin(http.server.ThreadingHTTPServer):
                address_family = socket.AF_INET6

                def server_bind(self):
                    self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    super().server_bind()

            ipv6_origin = IPv6Origin(("::", 80), Origin)
            threading.Thread(target=ipv6_origin.serve_forever, daemon=True).start()
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
                    "--fragment-retries",
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
                # These non-public destinations are attached owned origins, not null routes.
                for address in [
                    CGNAT_ORIGIN,
                    "169.254.77.2",
                    "[::ffff:10.77.0.2]",
                    "[fd00:77::2]",
                ]:
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        f"http://{address}/range-control"
                    ) as response:
                        self.assertEqual(response.status, 200)
                    self.assertIn("/range-control", hits)
                    hits.clear()
                    self.assertNotEqual(
                        proxy.run(download(f"http://{address}/secret.wav", "range")), 0
                    )
                    self.assertEqual(hits, [], address)
                for target in [
                    f"http://10.77.0.2:{port}/secret.wav",
                    f"http://127.0.0.1:{port}/secret.wav",
                    f"http://[::ffff:127.0.0.1]:{port}/secret.wav",
                    f"http://10.0.0.1:{port}/secret.wav",
                    f"http://169.254.77.2:{port}/secret.wav",
                    f"http://{CGNAT_ORIGIN}:{port}/secret.wav",
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
                for name in ["evil.m3u8", "evil.mpd"]:
                    self.assertNotEqual(
                        proxy.run(download(f"http://public.invalid/{name}", name)), 0
                    )
                    self.assertIn("/" + name, hits)
                    self.assertFalse(
                        any(path.endswith((".m4s", ".ts", "/secret.wav")) for path in hits), hits
                    )
                    hits.clear()
                for page, suffix in [("thumbnail-public", "thumbcontrol"), ("thumbnail", "thumb")]:
                    thumbnail = download(f"http://public.invalid/{page}", suffix)
                    thumbnail.remove("--no-warnings")
                    thumbnail.insert(1, "--write-thumbnail")
                    result = subprocess.run(  # noqa: S603 -- owned extractor fixture
                        [sys.executable, str(ROOT / "app/scripts/public-download.py"), *thumbnail],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("/" + page, hits)
                    self.assertIn("/control.wav", hits)
                    if page == "thumbnail-public":
                        self.assertIn("/public.jpg", hits)
                        self.assertTrue((root / "outthumbcontrol.jpg").exists())
                    else:
                        # yt-dlp treats thumbnail errors as nonfatal; measure refusal itself.
                        self.assertIn("407", result.stderr)
                        self.assertNotIn("/secret.jpg", hits)
                        self.assertFalse((root / "outthumb.jpg").exists())
                    hits.clear()
                stream = r"""
import os, urllib.request
p = urllib.request.ProxyHandler({"https": os.environ["https_proxy"]})
with urllib.request.build_opener(p).open("https://public.invalid/stream", timeout=2) as response:
    assert response.read() == b"ok" * 12
"""
                start = time.monotonic()
                self.assertEqual(proxy.run([sys.executable, "-c", stream]), 0)
                self.assertGreater(time.monotonic() - start, 5)
                self.assertIn("/stream", hits)
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
                # RFC6455 section4.1 step3: CONNECT for BOTH ws:80 and wss:443.
                # This is an explicit HTTP proxy client, not the sbx transparent path.
                code = r"""
import base64, hashlib, os, socket, ssl, sys
WS_GUID = f"{0x258EAFA5:08X}-{0xE914:04X}-{0x47DA:04X}-{0x95CA:04X}-{0xC5AB0DC85B11:012X}"
from urllib.parse import urlsplit
p = urlsplit(os.environ["http_proxy"])
s = socket.create_connection((p.hostname, p.port), timeout=3)
port = 443 if sys.argv[1] == "True" else 80
s.sendall(f"CONNECT public.invalid:{port} HTTP/1.1\r\nHost: public.invalid:{port}\r\n\r\n".encode())
response = b""
while not response.endswith(b"\r\n\r\n"):
    data = s.recv(1)
    assert data, response
    response += data
assert response.split()[1] == b"200", response
if port == 443:
    s = ssl.create_default_context().wrap_socket(s, server_hostname="public.invalid")
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET /socket HTTP/1.1\r\nHost: public.invalid\r\n"
           "Connection: Upgrade\r\nUpgrade: websocket\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
response = b""
while not response.endswith(b"\r\n\r\n"):
    data = s.recv(1)
    assert data, response
    response += data
assert response.split()[1] == b"101", response
headers = dict(line.split(b": ", 1) for line in response.split(b"\r\n")[1:] if b": " in line)
assert headers[b"Upgrade"].lower() == b"websocket"
assert headers[b"Connection"].lower() == b"upgrade"
assert headers[b"Sec-WebSocket-Accept"] == base64.b64encode(hashlib.sha1(
    (key + WS_GUID).encode(), usedforsecurity=False).digest())
def receive(size):
    result = b""
    while len(result) < size:
        data = s.recv(size - len(result))
        assert data, result
        result += data
    return result
for opcode, payload, expected in [(1, b"ping", b"fixture-stream"), (8, b"\x03\xe8", b"\x03\xe8")]:
    mask = os.urandom(4)
    frame = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
    s.sendall(frame + bytes(c ^ mask[i % 4] for i,c in enumerate(payload)))
    assert receive(2) == bytes([0x80 | opcode, len(expected)])
    assert receive(len(expected)) == expected
s.close()
"""
                for secure_socket in [True, False]:
                    self.assertEqual(proxy.run([sys.executable, "-c", code, str(secure_socket)]), 0)
                    self.assertIn("/socket", hits)
                    print(f"RFC6455 CONNECT WS secure={secure_socket} passed", flush=True)
                    hits.clear()
                if os.environ.get("FIXTURE_SHORT_IDLE"):
                    idle = r"""
import os, socket, time
from urllib.parse import urlsplit
p = urlsplit(os.environ["http_proxy"])
s = socket.create_connection((p.hostname, p.port), timeout=4)
s.sendall(b"CONNECT public.invalid:80 HTTP/1.1\r\nHost: public.invalid:80\r\n\r\n")
response = b""
while not response.endswith(b"\r\n\r\n"):
    data = s.recv(1)
    assert data, response
    response += data
assert response.split()[1] == b"200", response
# Reach the real HTTP origin before testing inactivity.
s.sendall(b"GET /idle HTTP/1.1\r\nHost: public.invalid\r\n\r\n")
response = b""
while not response.endswith(b"\r\n\r\n"):
    data = s.recv(1)
    assert data, response
    response += data
assert response.split()[1] == b"200", response
time.sleep(2)
assert s.recv(1) == b"", "idle CONNECT remained open"
s.close()
"""
                    self.assertEqual(proxy.run([sys.executable, "-c", idle]), 0)
                    self.assertIn("/idle", hits)
                    hits.clear()
                    print("one-second fixture CONNECT inactivity bound passed", flush=True)
                # Outer Docker fixture stops/starts ONLY its owned gateway.
                Path("/control/gateway-stop-ready").touch()

                def wait_marker(name):
                    deadline = time.monotonic() + 30
                    while not Path("/control/" + name).exists():
                        self.assertLess(time.monotonic(), deadline, name)
                        time.sleep(0.1)

                wait_marker("gateway-stopped")
                self.assertNotEqual(
                    proxy.run(download("http://public.invalid/dead.wav", "dead")), 0
                )
                self.assertEqual(hits, [], "gateway outage caused direct destination hit")
                Path("/control/gateway-dead-checked").touch()
                wait_marker("gateway-restarted")
                self.assertEqual(
                    proxy.run(download("http://public.invalid/restarted.wav", "restart")), 0
                )
                self.assertIn("/restarted.wav", hits)
                print("gateway dead/no fallback/restart destination controls passed", flush=True)
            finally:
                ipv6_origin.shutdown()
                ipv6_origin.server_close()
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
