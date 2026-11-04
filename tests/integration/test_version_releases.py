"""Public release HTTP reads through the real registry, independently of its stamp."""
import asyncio
import json
from datetime import datetime

import pytest
from aiohttp import web

from nora.agent.registry import ToolRegistry
from nora.infra.releases import GithubReleases
from nora.infra.version import VersionManifest
from nora.obs.metrics import Metrics


def release(version, *, body=None, prerelease=False):
    return {"tag_name": version, "published_at": "2026-01-03T00:00:00Z",
            "draft": False, "prerelease": prerelease,
            "body": body if body is not None else f"Changes in {version}"}


@pytest.fixture
async def github(aiohttp_server):
    state = {"latest": release("v0.0.3"),
             "entries": [release("v0.0.4-rc1", prerelease=True), release("v0.0.3"),
                         release("v0.0.2"), release("v0.0.1")],
             "status": 200, "requests": [], "pause": False, "resume": asyncio.Event()}

    async def handler(request):
        assert "Authorization" not in request.headers
        state["requests"].append((request.path, dict(request.query)))
        if state["pause"]:
            await state["resume"].wait()
        if state["status"] != 200:
            return web.json_response({"message": "Synthetic provider refusal"},
                                     status=state["status"])
        if request.path == "/releases/latest":
            return web.json_response(state["latest"])
        if request.path == "/releases":
            return web.json_response(state["entries"][:int(request.query.get("per_page", "30"))])
        if request.path.startswith("/releases/tags/"):
            tag = request.path.removeprefix("/releases/tags/")
            entry = next((e for e in state["entries"] if e["tag_name"] == tag), None)
            if entry is not None:
                return web.json_response(entry)
        return web.json_response({"message": "Not Found"}, status=404)

    app = web.Application()
    app.router.add_get('/{tail:.*}', handler)
    server = await aiohttp_server(app)
    state["url"] = str(server.make_url('/')).rstrip('/')
    yield state
    state["resume"].set()


def tools(tmp_path, github, *, running='v0.0.1', timeout=5):
    path = tmp_path / 'stamp.json'
    if running is not None:
        path.write_text(json.dumps({"version": running, "versions": [
            {"version": running, "commit": "synthetic-running-sha", "notes": "Baked notes"}]}))
    registry = ToolRegistry(versions=VersionManifest(path),
                           releases=GithubReleases(base_url=github['url'], timeout_seconds=timeout))
    specs = registry.specs(store=None, conversation_id='room', conversation_type='group',
                           persons=None, honcho=None, deliverer=None, metrics=Metrics(), room=None)
    return {s.name: s.call for s in specs}, path


@pytest.mark.parametrize('running,expected', [
    ('v0.0.1', 'behind'), ('v0.0.3', 'same'), ('v0.0.4', 'ahead'),
    ('v0.0.3-2-gabcdef', 'unknown'), ('v00.0.3', 'unknown'),
])
async def test_running_identity_is_independent_of_the_latest_release(
    tmp_path, github, running, expected,
):
    calls, _ = tools(tmp_path, github, running=running)
    answer = await calls['version_list']()
    assert answer['status'] == 'ok'
    assert answer['running']['version'] == running
    assert answer['running']['commit'] == 'synthetic-running-sha'
    assert answer['published']['latest']['version'] == 'v0.0.3'
    assert answer['published']['source'] == 'github'
    assert datetime.fromisoformat(answer['published']['observed_at']).tzinfo is not None
    assert 'notes' not in answer['published']['latest']
    assert answer['comparison'] == expected
    assert github['requests'] == [('/releases/latest', {})]
    assert 'do not prove deployment' in answer['note']


async def test_a_live_release_advances_while_running_identity_stays_fixed(tmp_path, github):
    calls, path = tools(tmp_path, github)
    first = await calls['version_list']()
    path.write_text(json.dumps({'version': 'v0.0.9'}))
    github['latest'] = release('v0.0.4')
    second = await calls['version_list']()
    assert second['running'] == first['running']
    assert second['published']['latest']['version'] == 'v0.0.4'


async def test_history_discovers_intermediate_releases_without_claiming_they_are_running(
    tmp_path, github,
):
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_list'](history=True)
    assert [r['version'] for r in answer['published']['releases']] == [
        'v0.0.4-rc1', 'v0.0.3', 'v0.0.2', 'v0.0.1']
    assert answer['published']['releases'][0]['prerelease'] is True
    assert answer['published']['history_complete'] is True
    assert answer['running']['version'] == 'v0.0.1' and answer['comparison'] == 'unknown'
    assert github['requests'] == [('/releases', {'per_page': '20'})]


async def test_a_full_history_page_does_not_claim_the_entire_gap_is_covered(tmp_path, github):
    github['entries'] = [release(f'v0.1.{n}') for n in range(30, 0, -1)]
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_list'](history=True)
    assert len(answer['published']['releases']) == 20
    assert answer['published']['history_complete'] is False


@pytest.mark.parametrize('typed,canonical', [('v0.0.3', 'v0.0.3'), ('V0.0.3', 'v0.0.3'),
                                             (' 0.0.3 ', 'v0.0.3'),
                                             ('v0.0.4-rc1', 'v0.0.4-rc1')])
async def test_read_uses_published_notes_even_when_the_image_never_recorded_them(
    tmp_path, github, typed, canonical,
):
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_read'](version=typed)
    assert answer['status'] == 'ok' and answer['source'] == 'github'
    assert answer['version'] == canonical and answer['notes'] == f'Changes in {canonical}'
    assert answer['notes_truncated'] is False
    assert 'commit' not in answer   # target_commitish is not an immutable deployed SHA
    assert github['requests'] == [(f'/releases/tags/{canonical}', {})]


@pytest.mark.parametrize('status', [403, 429, 500])
async def test_a_github_failure_does_not_hide_the_running_version(tmp_path, github, status):
    github['status'] = status
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_list']()
    assert answer['status'] == 'unavailable' and answer['running']['version'] == 'v0.0.1'
    assert answer['published']['status'] == 'unavailable' and answer['comparison'] == 'unknown'
    read = await calls['version_read'](version='v0.0.3')
    assert read['status'] == 'unavailable' and 'notes' not in read
    fallback = await calls['version_read'](version='v0.0.1')
    assert fallback['status'] == 'ok' and fallback['source'] == 'shipped_snapshot'
    assert fallback['published_status'] == 'unavailable' and fallback['notes'] == 'Baked notes'


async def test_a_missing_stamp_does_not_hide_published_versions(tmp_path, github):
    calls, _ = tools(tmp_path, github, running=None)
    answer = await calls['version_list']()
    assert answer['status'] == 'unavailable' and answer['running']['status'] == 'unavailable'
    assert answer['published']['latest']['version'] == 'v0.0.3'
    assert answer['comparison'] == 'unknown'
    assert (await calls['version_read'](version='v0.0.3'))['status'] == 'ok'


async def test_an_unknown_release_is_distinct_from_a_failed_check(tmp_path, github):
    calls, _ = tools(tmp_path, github)
    missing = await calls['version_read'](version='v9.9.9')
    assert missing['status'] == 'nothing_found'
    github['status'] = 503
    assert (await calls['version_read'](version='v9.9.9'))['status'] == 'unavailable'


@pytest.mark.parametrize('payload', [{}, [], {'tag_name': 'v0.0.3'},
                                    release('v0.0.3', prerelease=True)])
async def test_a_malformed_latest_response_does_not_invent_a_release(tmp_path, github, payload):
    github['latest'] = payload
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_list']()
    assert answer['published']['status'] == 'unavailable'
    assert answer['running']['version'] == 'v0.0.1'
    assert 'latest' not in answer['published'] and answer['comparison'] == 'unknown'


async def test_a_hung_github_read_times_out_without_losing_local_identity(tmp_path, github):
    github['pause'] = True
    calls, _ = tools(tmp_path, github, timeout=0.01)
    try:
        answer = await asyncio.wait_for(calls['version_list'](), timeout=1)
    finally:
        github['resume'].set()
    assert answer['running']['version'] == 'v0.0.1'
    assert answer['published']['status'] == 'unavailable'


async def test_an_empty_history_is_not_a_network_failure(tmp_path, github):
    github['entries'] = []
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_list'](history=True)
    assert answer['published']['status'] == 'nothing_found'
    assert answer['published']['releases'] == []
    github['status'] = 404
    assert (await calls['version_list']())['published']['status'] == 'unavailable'


async def test_truncated_notes_are_labelled_and_not_claimed_complete(tmp_path, github):
    github['entries'][1]['body'] = 'x' * 8001
    calls, _ = tools(tmp_path, github)
    answer = await calls['version_read'](version='v0.0.3')
    assert answer['notes'] == 'x' * 8000 and answer['notes_truncated'] is True


async def test_a_recorded_release_without_a_public_github_release_uses_labelled_history(
    tmp_path, github,
):
    calls, _ = tools(tmp_path, github)
    github['entries'] = []
    answer = await calls['version_read'](version='v0.0.1')
    assert answer['status'] == 'ok' and answer['source'] == 'shipped_snapshot'
    assert answer['published_status'] == 'nothing_found' and answer['notes'] == 'Baked notes'
