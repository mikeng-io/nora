"""Disposable pinned Ory services for generated managed-identity integration journeys."""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

KRATOS_IMAGE = (
    "oryd/kratos:v26.2.0"
    "@sha256:2a13bb8d362c7a7ae33bd7c0f5168aee46921f15c916a06346db91c06dc76643"
)

KETO_IMAGE = (
    "oryd/keto:v26.2.0"
    "@sha256:bfdb8b9e283aedbd9e1d005c44eb1bbefd2096b2cb5b5aa0b7360b6e39c178d2"
)


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — fixed docker argv, owned names
        ["docker", *args], check=True, capture_output=True, text=True,
        timeout=timeout,
    )


def _mapped_port(name: str, container_port: int) -> str:
    out = _docker("port", name, f"{container_port}/tcp").stdout.strip()
    return out.rsplit(":", 1)[-1]


def _wait_ready(
    urls: dict[str, str],
    containers: dict[str, str],
    *,
    deadline_seconds: float = 90.0,
) -> None:
    """Wait only for owned service readiness, never to order journey state."""
    started = time.monotonic()
    pending = dict(urls)
    while pending:
        for name, url in list(pending.items()):
            try:
                with urllib.request.urlopen(  # noqa: S310 — owned loopback health URLs
                        url, timeout=2) as response:
                    if response.status == 200:
                        pending.pop(name)
                        continue
            except (OSError, urllib.error.URLError):
                pass
        if not pending:
            return
        if time.monotonic() - started > deadline_seconds:
            logs = {
                name: _docker("logs", "--tail", "20", containers[name]).stdout[-1500:]
                for name in pending
            }
            pytest.fail(
                f"Ory containers not ready after {deadline_seconds}s: "
                f"{sorted(pending)}; tails: {logs}"
            )
        time.sleep(0.25)


@dataclass
class _Ory:
    kratos_admin: str
    kratos_public: str
    keto_read: str
    keto_write: str
    containers: tuple[str, str]


@contextmanager
def ory_stack(snapshot: Path | None = None) -> Iterator[_Ory]:
    """Disposable pinned Ory containers on dynamic loopback ports — owned
    entirely by this journey and removed by name in teardown. The lifecycle
    mirrors deploy/ory/compose.yaml step for step: data volumes, owner fix,
    one-shot migrations, then serving containers."""
    tag = f"nora-mit-{uuid.uuid4().hex[:10]}"
    keto_vol, kratos_vol = f"{tag}-keto", f"{tag}-kratos"
    keto_name, kratos_name = f"{tag}-keto", f"{tag}-kratos"
    root = Path(__file__).resolve().parents[2]
    configuration = snapshot / "configuration" if snapshot is not None else root / "deploy/ory"
    keto_cfg, kratos_cfg = str(configuration / "keto"), str(configuration / "kratos")
    for image in (KETO_IMAGE, KRATOS_IMAGE):
        _docker("image", "inspect", image)
    try:
        _docker("volume", "create", keto_vol)
        _docker("volume", "create", kratos_vol)
        for image, vol in ((KETO_IMAGE, keto_vol), (KRATOS_IMAGE, kratos_vol)):
            _docker("run", "--pull=never", "--rm", "--user", "0:0",
                    "--entrypoint", "chown", "-v",
                    f"{vol}:/home/ory/data", image,
                    "ory:ory", "/home/ory/data")
        if snapshot is not None:
            for image, vol, name in ((KETO_IMAGE, keto_vol, "keto"),
                                     (KRATOS_IMAGE, kratos_vol, "kratos")):
                _docker("run", "--pull=never", "--rm", "--user", "0:0",
                        "--entrypoint", "cp", "-v", f"{snapshot / name}:/backup:ro",
                        "-v", f"{vol}:/home/ory/data", image,
                        "-a", "/backup/.", "/home/ory/data/")
                _docker("run", "--pull=never", "--rm", "--user", "0:0",
                        "--entrypoint", "chown", "-v", f"{vol}:/home/ory/data", image,
                        "-R", "ory:ory", "/home/ory/data")
        else:
            _docker("run", "--pull=never", "--rm",
                    "-v", f"{keto_cfg}:/etc/keto:ro", "-v",
                    f"{keto_vol}:/home/ory/data",
                    "-e", "DSN=sqlite:///home/ory/data/keto.db?_fk=true",
                    "--entrypoint", "keto", KETO_IMAGE,
                    "migrate", "up", "-c", "/etc/keto/keto.dev.yaml", "--yes")
            _docker("run", "--pull=never", "--rm",
                    "-v", f"{kratos_cfg}:/etc/kratos:ro", "-v",
                    f"{kratos_vol}:/home/ory/data",
                    "-e", "DSN=sqlite:///home/ory/data/kratos.db?_fk=true",
                    "--entrypoint", "kratos", KRATOS_IMAGE,
                    "migrate", "sql", "-e", "--yes")
        _docker("run", "--pull=never", "-d", "--name", keto_name,
                "-v", f"{keto_cfg}:/etc/keto:ro", "-v",
                f"{keto_vol}:/home/ory/data",
                "-e", "DSN=sqlite:///home/ory/data/keto.db?_fk=true",
                "-e", "SERVE_READ_HOST=0.0.0.0",
                "-e", "SERVE_WRITE_HOST=0.0.0.0",
                "-p", "127.0.0.1:0:4466", "-p", "127.0.0.1:0:4467",
                KETO_IMAGE, "serve", "-c", "/etc/keto/keto.dev.yaml")
        _docker("run", "--pull=never", "-d", "--name", kratos_name,
                "-v", f"{kratos_cfg}:/etc/kratos:ro", "-v",
                f"{kratos_vol}:/home/ory/data",
                "-e", "DSN=sqlite:///home/ory/data/kratos.db?_fk=true",
                "-e", "SERVE_PUBLIC_HOST=0.0.0.0",
                "-e", "SERVE_ADMIN_HOST=0.0.0.0",
                "-p", "127.0.0.1:0:4433", "-p", "127.0.0.1:0:4434",
                KRATOS_IMAGE, "serve", "--dev", "-c",
                "/etc/kratos/kratos.dev.yaml")
        keto_read = _mapped_port(keto_name, 4466)
        keto_write = _mapped_port(keto_name, 4467)
        kratos_public = _mapped_port(kratos_name, 4433)
        kratos_admin = _mapped_port(kratos_name, 4434)
        # The same readiness endpoints the compose healthchecks use.
        _wait_ready(
            {
                "keto": f"http://127.0.0.1:{keto_read}/health/ready",
                "kratos": f"http://127.0.0.1:{kratos_admin}/admin/health/ready",
            },
            {"keto": keto_name, "kratos": kratos_name},
        )
        yield _Ory(
            kratos_admin=f"http://127.0.0.1:{kratos_admin}",
            kratos_public=f"http://127.0.0.1:{kratos_public}",
            keto_read=f"http://127.0.0.1:{keto_read}",
            keto_write=f"http://127.0.0.1:{keto_write}",
            containers=(keto_name, kratos_name),
        )
    finally:
        # Teardown removes exactly the resources this journey named — never a
        # wildcard sweep over somebody else's containers.
        subprocess.run(  # noqa: S603 — exact owned container names
            ["docker", "rm", "-f", keto_name, kratos_name],
            capture_output=True, timeout=60)
        subprocess.run(  # noqa: S603 — the journey's own volume names
            ["docker", "volume", "rm", keto_vol, kratos_vol],
            capture_output=True, timeout=60)
        remaining = _docker("ps", "-aq", "--filter", f"name={tag}-")
        assert not remaining.stdout.split(), (
            f"journey teardown left containers behind: {remaining.stdout!r}")
        left = _docker("volume", "ls", "-q", "--filter", f"name={tag}-")
        assert not left.stdout.split(), (
            f"journey teardown left volumes behind: {left.stdout!r}")


@pytest.fixture
def ory() -> Iterator[_Ory]:
    with ory_stack() as stack:
        yield stack


def snapshot_ory(stack: _Ory, directory: Path) -> None:
    """Capture stopped SQLite stores; leave original services quiesced."""
    _docker("stop", *stack.containers)
    source = Path(__file__).resolve().parents[2] / "deploy/ory"
    for name in ("keto", "kratos"):
        shutil.copytree(source / name, directory / "configuration" / name)
    for name, container in zip(("keto", "kratos"), stack.containers, strict=True):
        target = directory / name
        target.mkdir(mode=0o700, parents=True)
        _docker("cp", f"{container}:/home/ory/data/.", str(target))


@pytest.fixture
def keto() -> Iterator[tuple[str, str]]:
    """The pinned Keto half of `ory` alone — adapter/tuple reads that need
    the real service but not Kratos. Yields (read_url, write_url)."""
    tag = f"nora-mit-{uuid.uuid4().hex[:10]}"
    name = f"{tag}-keto"
    root = Path(__file__).resolve().parents[2]
    keto_cfg = f"{root}/deploy/ory/keto"
    _docker("image", "inspect", KETO_IMAGE)
    try:
        _docker("volume", "create", name)
        _docker("run", "--pull=never", "--rm", "--user", "0:0",
                "--entrypoint", "chown", "-v", f"{name}:/home/ory/data",
                KETO_IMAGE, "ory:ory", "/home/ory/data")
        _docker("run", "--pull=never", "--rm",
                "-v", f"{keto_cfg}:/etc/keto:ro", "-v", f"{name}:/home/ory/data",
                "-e", "DSN=sqlite:///home/ory/data/keto.db?_fk=true",
                "--entrypoint", "keto", KETO_IMAGE,
                "migrate", "up", "-c", "/etc/keto/keto.dev.yaml", "--yes")
        _docker("run", "--pull=never", "-d", "--name", name,
                "-v", f"{keto_cfg}:/etc/keto:ro", "-v", f"{name}:/home/ory/data",
                "-e", "DSN=sqlite:///home/ory/data/keto.db?_fk=true",
                "-e", "SERVE_READ_HOST=0.0.0.0", "-e", "SERVE_WRITE_HOST=0.0.0.0",
                "-p", "127.0.0.1:0:4466", "-p", "127.0.0.1:0:4467",
                KETO_IMAGE, "serve", "-c", "/etc/keto/keto.dev.yaml")
        read_port, write_port = _mapped_port(name, 4466), _mapped_port(name, 4467)
        _wait_ready(
            {"keto": f"http://127.0.0.1:{read_port}/health/ready"}, {"keto": name})
        yield f"http://127.0.0.1:{read_port}", f"http://127.0.0.1:{write_port}"
    finally:
        subprocess.run(  # noqa: S603 — exact owned container name
            ["docker", "rm", "-f", name], capture_output=True, timeout=60)
        subprocess.run(  # noqa: S603 — the journey's own volume name
            ["docker", "volume", "rm", name], capture_output=True, timeout=60)
