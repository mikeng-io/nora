"""Synthetic GitHub responses exercise the real public HTTP adapter."""

import httpx
import pytest

from nora.infra.github_releases import PAGE_SIZE, RELEASES_URL, GitHubReleases


def _release(version="v0.0.3", date="2026-01-03T00:00:00Z", **extra):
    return {"tag_name": version, "published_at": date,
            "html_url": f"https://github.com/mikeng-io/nora/releases/tag/{version}",
            "draft": False, "prerelease": False, "body": "Synthetic release notes.", **extra}


async def test_list_filters_sorts_and_omits_bodies_with_an_explicit_page_ceiling():
    def respond(request):
        assert request.method == "GET"
        assert str(request.url.copy_with(query=None)) == RELEASES_URL
        assert dict(request.url.params) == {"per_page": str(PAGE_SIZE), "page": "1"}
        assert "authorization" not in request.headers
        assert request.headers["accept"] == "application/vnd.github+json"
        return httpx.Response(200, json=[
            _release("v0.0.1", "2026-01-01T00:00:00Z"),
            _release(prerelease=True), _release(draft=True),
            _release(published_at=None), _release(),
        ], headers={"Link": f'<{RELEASES_URL}?page=2>; rel="next"'})

    result = await GitHubReleases(transport=httpx.MockTransport(respond)).list()
    assert result == {"status": "ok", "releases": [
        {"version": "v0.0.3", "date": "2026-01-03T00:00:00Z",
         "url": "https://github.com/mikeng-io/nora/releases/tag/v0.0.3"},
        {"version": "v0.0.1", "date": "2026-01-01T00:00:00Z",
         "url": "https://github.com/mikeng-io/nora/releases/tag/v0.0.1"},
    ], "more_available": True}


async def test_read_fetches_the_exact_selected_tag_body_and_distinguishes_absence():
    def respond(request):
        assert request.url.host == "api.github.com"
        if request.url.path == "/repos/mikeng-io/nora/releases/tags/v0.0.1":
            return httpx.Response(200, json=_release("v0.0.1", body="# Exact\n\nSynthetic notes."))
        return httpx.Response(404, json={"message": "Not Found"})

    api = GitHubReleases(transport=httpx.MockTransport(respond))
    result = await api.read("v0.0.1")
    assert result["status"] == "ok"
    assert result["version"] == "v0.0.1"
    assert result["body"] == "# Exact\n\nSynthetic notes."
    assert await api.read("v0.0.9") == {"status": "nothing_found"}
    assert await api.list() == {"status": "unavailable"}  # a list 404 is an API failure


@pytest.mark.parametrize("operation", ["list", "read"])
@pytest.mark.parametrize("failure", [403, 429, 503, "timeout", "json", "shape"])
async def test_api_failures_are_unavailable(operation, failure):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if failure == "json":
            return httpx.Response(200, text="not json")
        if failure == "shape":
            return httpx.Response(200, json=[{}] if operation == "list" else {})
        return httpx.Response(failure, json={"message": "Synthetic failure"})

    api = GitHubReleases(transport=httpx.MockTransport(respond))
    result = await api.list() if operation == "list" else await api.read("v0.0.1")
    assert result == {"status": "unavailable"}


async def test_empty_and_unpublished_results_are_distinct_from_failed_reads():
    def respond(request):
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/v0.0.1"):
            return httpx.Response(200, json=_release(body=None))
        return httpx.Response(200, json=_release(prerelease=True))

    api = GitHubReleases(transport=httpx.MockTransport(respond))
    assert await api.list() == {"status": "nothing_found", "releases": [], "more_available": False}
    assert (await api.read("v0.0.1"))["body"] == ""
    assert await api.read("v0.0.2") == {"status": "nothing_found"}
    assert await api.read("") == {"status": "unavailable"}


async def test_a_page_with_only_prereleases_does_not_claim_all_history_is_empty():
    def respond(request):
        return httpx.Response(200, json=[_release(prerelease=True)], headers={
            "Link": f'<{RELEASES_URL}?page=2>; rel="next"',
        })

    result = await GitHubReleases(transport=httpx.MockTransport(respond)).list()
    assert result == {"status": "ok", "releases": [], "more_available": True}
