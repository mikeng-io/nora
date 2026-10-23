"""Release tools through the real registry, HTTP adapter and immutable stamp."""

import json

import httpx
import pytest

from nora.agent.registry import ToolRegistry
from nora.infra.github_releases import GitHubReleases
from nora.infra.version import VersionManifest
from nora.obs.metrics import Metrics


def _tools(tmp_path, *, stamped=True, api_status=200):
    path = tmp_path / "version.json"
    if stamped:
        path.write_text(json.dumps({"version": "v0.0.1", "versions": []}))

    def respond(request):
        assert "authorization" not in request.headers
        release = {"tag_name": "v0.0.3", "published_at": "2026-01-03T00:00:00Z",
                   "html_url": "https://github.com/mikeng-io/nora/releases/tag/v0.0.3",
                   "body": "Synthetic new feature.", "draft": False, "prerelease": False}
        if api_status != 200:
            return httpx.Response(api_status)
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=[release])
        if request.url.path.endswith("/tags/v0.0.3"):
            return httpx.Response(200, json=release)
        return httpx.Response(404)

    registry = ToolRegistry(versions=VersionManifest(path), releases=GitHubReleases(
        transport=httpx.MockTransport(respond)))
    metrics = Metrics()
    specs = registry.specs(store=None, conversation_id="g1", conversation_type="group",
                           persons=None, honcho=None, deliverer=None, metrics=metrics, room=None)
    assert "version" not in {spec.name for spec in specs}
    return {spec.name: spec.call for spec in specs}, metrics, path


async def test_published_releases_do_not_change_the_immutable_running_version(tmp_path):
    tools, metrics, path = _tools(tmp_path)
    listed = await tools["version_list"]()
    path.write_text(json.dumps({"version": "v0.0.9"}))
    again = await tools["version_list"]()
    assert listed["running"] == again["running"] == {"status": "ok", "version": "v0.0.1"}
    assert listed["releases"][0]["version"] == "v0.0.3"
    assert "body" not in listed["releases"][0]
    assert "not deployment" in listed["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_list", status="ok") == 2


async def test_read_uses_github_body_not_the_local_manifest(tmp_path):
    tools, metrics, _ = _tools(tmp_path)
    answer = await tools["version_read"](version="v0.0.3")
    assert answer["status"] == "ok"
    assert answer["body"] == "Synthetic new feature."
    assert "does not prove deployment" in answer["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="ok") == 1


@pytest.mark.parametrize("stamped,api_status", [(False, 200), (True, 503), (False, 503)])
async def test_running_and_release_statuses_remain_independent(tmp_path, stamped, api_status):
    tools, metrics, _ = _tools(tmp_path, stamped=stamped, api_status=api_status)
    listed = await tools["version_list"]()
    assert listed["status"] == ("ok" if api_status == 200 else "unavailable")
    assert listed["running"]["status"] == ("ok" if stamped else "unavailable")
    if not stamped:
        assert "version" not in listed["running"] and listed["running"]["note"]
    read = await tools["version_read"](version="v0.0.3")
    assert read["status"] == listed["status"]
    assert listed["note"] and read["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_list", status=listed["status"]) == 1


async def test_absent_release_and_invalid_input_are_not_api_failures_or_empty_success(tmp_path):
    tools, metrics, _ = _tools(tmp_path)
    absent = await tools["version_read"](version="v0.0.9")
    invalid = await tools["version_read"](version="")
    assert absent["status"] == "nothing_found" and absent["note"]
    assert invalid["status"] == "unavailable" and invalid["note"]
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="nothing_found") == 1
    assert metrics.get("nora_tool_calls_total", tool="version_read", status="unavailable") == 1


async def test_an_unwired_registry_stays_unavailable_without_network(no_build_stamp):
    specs = ToolRegistry().specs(store=None, conversation_id="g1", conversation_type="group",
                                 persons=None, honcho=None, deliverer=None,
                                 metrics=Metrics(), room=None)
    tools = {spec.name: spec.call for spec in specs}
    listed = await tools["version_list"]()
    read = await tools["version_read"](version="v0.0.1")
    assert listed["status"] == listed["running"]["status"] == read["status"] == "unavailable"
    assert listed["note"] and read["note"]
