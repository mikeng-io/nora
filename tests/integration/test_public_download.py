"""Run real subprocesses in a disposable, internet-disconnected Linux fixture."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import docker
import yt_dlp

ROOT = Path(__file__).resolve().parents[2]


def test_real_downloader_boundary(tmp_path):
    shutil.copytree(Path(yt_dlp.__file__).parent, tmp_path / "yt_dlp")
    for source in [
        ROOT / "tests/fixtures/download-egress/Dockerfile",
        ROOT / "app/scripts/public-download.py",
        ROOT / "tests/integration/probe_public_download.py",
    ]:
        shutil.copy(source, tmp_path / source.name)
    client = docker.from_env()
    image = container = gateway = network = None
    try:
        image, _ = client.images.build(path=str(tmp_path), network_mode="host", rm=True)
        gateway_image, _ = client.images.build(
            path=str(ROOT / "deploy/http-egress"),
            network_mode="host",
            rm=True,
            tag="nora-http-egress-test:local",
        )
        network = client.networks.create(
            "nora-egress-" + uuid.uuid4().hex[:10],
            internal=True,
            ipam=docker.types.IPAMConfig(
                pool_configs=[docker.types.IPAMPool(subnet="93.184.216.0/24")]
            ),
        )
        gateway = client.containers.create(
            gateway_image.id,
            user="65532:65532",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            network_mode=network.name,
            dns=["93.184.216.34"],
            extra_hosts={"public.invalid": "93.184.216.34", "wrong.invalid": "93.184.216.34"},
        )
        network.disconnect(gateway)
        network.connect(gateway, ipv4_address="93.184.216.2", aliases=["http-egress"])
        gateway.start()
        container = client.containers.create(
            image.id,
            network_mode=network.name,
            security_opt=["seccomp=unconfined"],
        )
        network.disconnect(container)
        network.connect(container, ipv4_address="93.184.216.34")
        container.start()
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
        if network is not None:
            network.remove()
        if image is not None:
            client.images.remove(image.id)
        client.close()
