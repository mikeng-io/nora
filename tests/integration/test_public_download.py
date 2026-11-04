"""Run real subprocesses in a disposable, internet-disconnected Linux fixture."""

from __future__ import annotations

import shutil
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
    image = container = None
    try:
        image, _ = client.images.build(path=str(tmp_path), network_mode="host", rm=True)
        container = client.containers.run(
            image.id,
            detach=True,
            network_mode="none",
            cap_add=["NET_ADMIN"],
            security_opt=["seccomp=unconfined"],
        )
        result = container.wait(timeout=90)
        output = container.logs().decode()
        assert result["StatusCode"] == 0, output
        assert '"public_ytdlp": "passed"' in output
        assert '"https_host_sni_certificate": "passed"' in output
    finally:
        if container is not None:
            container.remove(force=True)
        if image is not None:
            client.images.remove(image.id)
        client.close()
