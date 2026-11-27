"""The scoped computer's registry directory is a host mount. Its absence must read as
itself, never as "no VM for this scope": `status()` names it, `/health` carries it without
failing the container, and a missing mount is not the same answer as an empty registry."""

from __future__ import annotations

import json
from pathlib import Path

from nora.doctrine.loader import DoctrineStore
from nora.domain.computer_vms import FileScopeEndpoints
from nora.obs.health import LLMState, build_health_app
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRecordStore
from tests.fakes import SinkSender

SCOPE = "a" * 32


def test_a_missing_registry_directory_is_degraded_and_says_the_mount_is_absent(tmp_path):
    status = FileScopeEndpoints(tmp_path / "scope-endpoints").status()

    assert status["state"] == "degraded"
    assert "mount is absent" in str(status["detail"])


def test_an_empty_registry_is_live_with_no_scopes_and_a_record_counts_as_one(tmp_path):
    registry = FileScopeEndpoints(tmp_path)
    assert registry.status() == {"state": "live", "scopes": 0, "detail": ""}

    (tmp_path / f"{SCOPE}.json").write_text(json.dumps(
        {"scope": SCOPE, "endpoint": "http://172.17.0.1:1", "name": "vm", "instance": "i"},
    ))

    assert registry.status() == {"state": "live", "scopes": 1, "detail": ""}


def test_an_unreadable_registry_is_degraded_with_its_own_cause_and_never_raises(
    tmp_path, monkeypatch,
):
    """A directory Nora cannot traverse is neither live nor "missing": startup must log the
    real cause and carry on, not abort. The permission error is injected at the filesystem
    boundary because a test running as root cannot be denied by mode bits."""
    def denied(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("nora.domain.computer_vms.os.scandir", denied)

    status = FileScopeEndpoints(tmp_path).status()

    assert status["state"] == "degraded"
    assert "unreadable" in str(status["detail"]) and "Permission denied" in str(status["detail"])


def _health_app(**kw):
    metrics = Metrics()
    return build_health_app(
        adapter=SinkSender(), store=FakeRecordStore(), honcho=FakeMemoryStore(),
        doctrine_store=DoctrineStore(Path("nora/doctrine"), canary="C", metrics=metrics),
        metrics=metrics, started_at=0.0, llm_state=LLMState(), **kw,
    )


async def test_health_reports_a_degraded_computer_registry_without_failing(
    aiohttp_client, tmp_path,
):
    absent = FileScopeEndpoints(tmp_path / "missing").status
    client = await aiohttp_client(_health_app(computer_health=absent))

    response = await client.get("/health")
    body = await response.json()

    assert response.status == 200
    assert body["computer"]["state"] == "degraded"


async def test_health_has_no_computer_block_when_the_scoped_computer_is_off(aiohttp_client):
    client = await aiohttp_client(_health_app())

    assert "computer" not in await (await client.get("/health")).json()


async def test_a_registry_read_that_raises_is_unavailable_not_a_crash(aiohttp_client):
    def boom():
        raise OSError("mount gone")

    client = await aiohttp_client(_health_app(computer_health=boom))

    response = await client.get("/health")

    assert response.status == 200
    assert (await response.json())["computer"] == {"state": "unavailable"}
