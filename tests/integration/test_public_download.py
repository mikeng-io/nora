"""Run real subprocesses in a disposable, internet-disconnected Linux fixture."""

from __future__ import annotations

import importlib.util
import ipaddress
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import docker
import pytest
import yt_dlp

ROOT = Path(__file__).resolve().parents[2]
# Generated CGNAT subnet, never a host's real Tailscale identity.
CGNAT = ipaddress.IPv4Network((0x64404D00, 24))


@pytest.mark.parametrize("short_idle", [False, True], ids=["production", "idle-1s"])
def test_real_downloader_boundary(tmp_path, short_idle):
    shutil.copytree(Path(yt_dlp.__file__).parent, tmp_path / "yt_dlp")
    for source in [
        ROOT / "tests/fixtures/download-egress/Dockerfile",
        ROOT / "app/scripts/public-download.py",
        ROOT / "tests/integration/probe_public_download.py",
    ]:
        shutil.copy(source, tmp_path / source.name)
    client = docker.from_env()
    range_networks = []
    ipv6_network = None
    image = container = gateway = network = private_network = None
    try:
        image, _ = client.images.build(path=str(tmp_path), network_mode="host", rm=True)
        gateway_context = ROOT / "deploy/http-egress"
        if short_idle:
            gateway_context = tmp_path / "gateway"
            shutil.copytree(ROOT / "deploy/http-egress", gateway_context)
            source = gateway_context / "main.go"
            original = source.read_text()
            assert original.count("c.IdleTimeout = 5 * time.Minute") == 1
            source.write_text(
                original.replace("c.IdleTimeout = 5 * time.Minute", "c.IdleTimeout = time.Second")
            )
        subprocess.run(  # noqa: S603 -- fixed local image recipe, no publish
            [
                "docker",
                "buildx",
                "build",
                "--load",
                "--platform",
                "linux/amd64",
                "--network=host",
                "-t",
                "nora-http-egress-test:amd64",
                str(gateway_context),
            ],
            check=True,
            capture_output=True,
        )
        gateway_image = client.images.get("nora-http-egress-test:amd64")
        assert gateway_image.attrs["Architecture"] == "amd64"
        network = client.networks.create(
            "nora-egress-" + uuid.uuid4().hex[:10],
            internal=True,
            ipam=docker.types.IPAMConfig(
                pool_configs=[docker.types.IPAMPool(subnet="93.184.216.0/24")]
            ),
        )
        private_network = client.networks.create(
            "nora-egress-private-" + uuid.uuid4().hex[:10],
            internal=True,
            ipam=docker.types.IPAMConfig(
                pool_configs=[docker.types.IPAMPool(subnet="10.77.0.0/24")]
            ),
        )
        for subnet in [str(CGNAT), "169.254.77.0/24"]:
            range_networks.append(
                client.networks.create(
                    "nora-egress-range-" + uuid.uuid4().hex[:10],
                    internal=True,
                    ipam=docker.types.IPAMConfig(
                        pool_configs=[docker.types.IPAMPool(subnet=subnet)]
                    ),
                )
            )
        ipv6_network = client.networks.create(
            "nora-egress-v6-" + uuid.uuid4().hex[:10],
            internal=True,
            enable_ipv6=True,
            ipam=docker.types.IPAMConfig(
                pool_configs=[docker.types.IPAMPool(subnet="fd00:77::/64")]
            ),
        )
        gateway = client.containers.create(
            gateway_image.id,
            user="65532:65532",
            environment={
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "http_proxy": "http://127.0.0.1:1",
                "https_proxy": "http://127.0.0.1:1",
            },
            read_only=True,
            mem_limit="256m",
            pids_limit=128,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            network_mode=network.name,
            dns=["93.184.216.34"],
            extra_hosts={"public.invalid": "93.184.216.34", "wrong.invalid": "93.184.216.34"},
        )
        network.disconnect(gateway)
        network.connect(gateway, ipv4_address="93.184.216.2", aliases=["http-egress"])
        private_network.connect(gateway, ipv4_address="10.77.0.3")
        for net, address in zip(
            range_networks, [str(CGNAT.network_address + 3), "169.254.77.3"], strict=True
        ):
            net.connect(gateway, ipv4_address=address)
        ipv6_network.connect(gateway, ipv6_address="fd00:77::3")
        gateway.start()
        container = client.containers.create(
            image.id,
            environment={"FIXTURE_SHORT_IDLE": "1" if short_idle else ""},
            network_mode=network.name,
            security_opt=["seccomp=unconfined"],
        )
        network.disconnect(container)
        network.connect(container, ipv4_address="93.184.216.34")
        private_network.connect(container, ipv4_address="10.77.0.2")
        for net, address in zip(
            range_networks, [str(CGNAT.network_address + 2), "169.254.77.2"], strict=True
        ):
            net.connect(container, ipv4_address=address)
        ipv6_network.connect(container, ipv6_address="fd00:77::2")
        container.start()

        def wait_marker(name):
            deadline = time.monotonic() + 160
            while True:
                try:
                    if container.exec_run(["test", "-f", "/control/" + name]).exit_code == 0:
                        return
                except docker.errors.APIError as error:
                    raise AssertionError(container.logs().decode()) from error
                container.reload()
                assert container.status == "running", container.logs().decode()
                assert time.monotonic() < deadline, container.logs().decode()
                time.sleep(0.2)

        wait_marker("gateway-stop-ready")
        gateway.reload()
        assert gateway.attrs["HostConfig"]["Memory"] == 256 * 1024 * 1024
        assert gateway.attrs["HostConfig"]["PidsLimit"] == 128
        assert not gateway.attrs["State"]["OOMKilled"]
        print(f"gateway image={gateway_image.id} short_idle={short_idle} limits=256MiB/128pids")
        gateway.stop(timeout=1)
        container.exec_run(["touch", "/control/gateway-stopped"])
        wait_marker("gateway-dead-checked")
        gateway.start()
        container.exec_run(["touch", "/control/gateway-restarted"])
        result = container.wait(timeout=180)
        output = container.logs().decode()
        assert result["StatusCode"] == 0, output + gateway.logs().decode()
        print(output)
        assert gateway.logs() == b"", "gateway emitted request diagnostics"
        assert '"public_ytdlp": "passed"' in output
        assert '"https_host_sni_certificate": "passed"' in output
    finally:
        if container is not None:
            container.remove(force=True)
        if gateway is not None:
            gateway.remove(force=True)
        for net in range_networks:
            net.remove()
        if ipv6_network is not None:
            ipv6_network.remove()
        if private_network is not None:
            private_network.remove()
        if network is not None:
            network.remove()
        if image is not None:
            client.images.remove(image.id)
        client.close()


def test_relay_drains_reverse_traffic_under_forward_backpressure():
    spec = importlib.util.spec_from_file_location(
        "download_relay", ROOT / "app/scripts/public-download.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    left, relay_left = socket.socketpair()
    relay_right, right = socket.socketpair()
    sockets = (left, relay_left, relay_right, right)
    for connection in sockets:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        connection.settimeout(3)
    payload = b"synthetic-transfer" * 131072
    received = {}
    errors = []

    def receive(connection):
        data = bytearray()
        while chunk := connection.recv(65536):
            data.extend(chunk)
        return bytes(data)

    def send_left():
        left.sendall(payload)
        left.shutdown(socket.SHUT_WR)

    def send_then_receive_right():
        right.sendall(payload)
        right.shutdown(socket.SHUT_WR)
        received["right"] = receive(right)

    def record_errors(operation):
        try:
            operation()
        except OSError as error:
            errors.append(type(error).__name__)

    pumps = [
        threading.Thread(target=module.relay, args=(relay_left, relay_right), daemon=True),
        threading.Thread(target=record_errors, args=(send_left,), daemon=True),
        threading.Thread(target=record_errors, args=(send_then_receive_right,), daemon=True),
    ]
    try:
        for thread in pumps:
            thread.start()
        received["left"] = receive(left)
        for thread in pumps:
            thread.join(timeout=4)
        assert not errors, errors
        assert all(not thread.is_alive() for thread in pumps)
        assert received == {"left": payload, "right": payload}
    finally:
        for connection in sockets:
            connection.close()
