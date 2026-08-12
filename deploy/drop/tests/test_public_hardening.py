"""End-to-end coverage for `hardening.PublicListenerHardening` as wired onto
`server.public_app`: `/robots.txt`, the `X-Robots-Tag` header on every
response type the public listener sends, and 403 bot rejection before any
route handler runs. `tests/test_bots.py` covers the UA pattern list itself in
isolation; this file covers the ASGI wiring around it.
"""

from __future__ import annotations

from conftest import TOKEN
from starlette.testclient import TestClient

from hardening import X_ROBOTS_TAG_VALUE

_BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _mint_slug(server, url: str = "https://example.com/target") -> str:
    internal = TestClient(server.internal_app)
    resp = internal.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": url},
    )
    return resp.json()["slug"]


def _register_artifact(server) -> str:
    internal = TestClient(server.internal_app)
    resp = internal.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hello", "text/plain")},
    )
    return resp.json()["url"].rsplit("/", 1)[-1]


# ── robots.txt ────────────────────────────────────────────────────────────


def test_robots_txt_body_and_content_type(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/robots.txt")

    assert resp.status_code == 200
    assert resp.text == "User-agent: *\nDisallow: /\n"
    assert resp.headers["content-type"].startswith("text/plain")


def test_robots_txt_is_reachable_by_a_bot_user_agent(wired_apps):
    """A bot has to be ABLE to fetch robots.txt to have any chance of
    respecting `Disallow: /` -- it must be exempt from the bot-rejection
    filter that blocks every other path."""
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/robots.txt", headers={"User-Agent": _BOT_UA})

    assert resp.status_code == 200
    assert resp.text == "User-agent: *\nDisallow: /\n"


# ── X-Robots-Tag on every response type ──────────────────────────────────


def test_x_robots_tag_on_health_check(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/health", headers={"User-Agent": _BROWSER_UA})

    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


def test_x_robots_tag_on_a_successful_redirect(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server)
    public = TestClient(server.public_app)

    resp = public.get(
        f"/i/{slug}", headers={"User-Agent": _BROWSER_UA}, follow_redirects=False,
    )

    assert resp.status_code == 301
    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


def test_x_robots_tag_on_a_successful_render(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _register_artifact(server)
    public = TestClient(server.public_app)

    resp = public.get(f"/drop/{slug}", headers={"User-Agent": _BROWSER_UA})

    assert resp.status_code == 200
    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


def test_x_robots_tag_on_a_404(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/i/doesnotexist", headers={"User-Agent": _BROWSER_UA})

    assert resp.status_code == 404
    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


def test_x_robots_tag_on_a_framework_level_404(wired_apps):
    """A path matching no route at all -- Starlette's own 404, not one of
    this app's handlers -- still gets the header because the middleware
    wraps the whole app, not each route."""
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/no/such/route", headers={"User-Agent": _BROWSER_UA})

    assert resp.status_code == 404
    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


def test_x_robots_tag_on_the_bot_rejection_response_itself(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server)
    public = TestClient(server.public_app)

    resp = public.get(f"/i/{slug}", headers={"User-Agent": _BOT_UA})

    assert resp.status_code == 403
    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


def test_x_robots_tag_on_robots_txt_itself(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/robots.txt")

    assert resp.headers["x-robots-tag"] == X_ROBOTS_TAG_VALUE


# ── bot rejection: 403 before any real work happens ──────────────────────


def test_a_bot_user_agent_is_403_on_the_redirect_route(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server)
    public = TestClient(server.public_app)

    resp = public.get(f"/i/{slug}", headers={"User-Agent": _BOT_UA})

    assert resp.status_code == 403


def test_a_bot_user_agent_is_403_on_the_render_route(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _register_artifact(server)
    public = TestClient(server.public_app)

    resp = public.get(f"/drop/{slug}", headers={"User-Agent": _BOT_UA})

    assert resp.status_code == 403


def test_a_missing_user_agent_is_also_403(wired_apps):
    """No User-Agent header at all -- `bots.is_bot_user_agent`'s documented
    fail-closed choice, exercised end to end."""
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server)
    public = TestClient(server.public_app)
    public.headers.pop("User-Agent", None)

    resp = public.get(f"/i/{slug}")

    assert resp.status_code == 403


def test_a_generic_scraper_shaped_user_agent_is_403(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server)
    public = TestClient(server.public_app)

    resp = public.get(f"/i/{slug}", headers={"User-Agent": "some-weird-scraper/3.1"})

    assert resp.status_code == 403


def test_a_normal_browser_user_agent_passes_through_to_the_real_redirect(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server, url="https://example.com/real-target")
    public = TestClient(server.public_app)

    resp = public.get(
        f"/i/{slug}", headers={"User-Agent": _BROWSER_UA}, follow_redirects=False,
    )

    assert resp.status_code == 301
    assert resp.headers["location"] == "https://example.com/real-target"


def test_a_normal_browser_user_agent_passes_through_to_the_real_render(wired_apps):
    server, _links, _artifacts = wired_apps
    slug = _register_artifact(server)
    public = TestClient(server.public_app)

    resp = public.get(f"/drop/{slug}", headers={"User-Agent": _BROWSER_UA})

    assert resp.status_code == 200
    assert resp.content == b"hello"


def test_a_bot_request_never_reaches_the_link_store(wired_apps):
    """The task's own requirement: reject BEFORE any real work happens (no
    redirect lookup). A bot hitting an UNMINTED slug still gets a plain 403,
    not a 404 -- proof the link store was never even consulted."""
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/i/totally-unminted-slug", headers={"User-Agent": _BOT_UA})

    assert resp.status_code == 403


def test_this_apps_own_tooling_user_agents_are_not_blocked(wired_apps):
    """curl's and python-requests'/aiohttp's default UAs -- what this task's
    own verification scripts, and this service's own docker HEALTHCHECK
    (Dockerfile), actually send. Must never trip the generic substring
    fallback."""
    server, _links, _artifacts = wired_apps
    slug = _mint_slug(server)
    public = TestClient(server.public_app)

    for ua in ("curl/8.5.0", "python-requests/2.31.0", "Python/3.12 aiohttp/3.9.1"):
        resp = public.get(f"/i/{slug}", headers={"User-Agent": ua}, follow_redirects=False)
        assert resp.status_code == 301, f"UA {ua!r} was unexpectedly blocked"


def test_the_internal_listener_is_not_hardened(wired_apps):
    """Task requirement: hardening applies to the PUBLIC listener only --
    the internal one already has no path to the internet (§5a), so a bot UA
    there must behave exactly as before (auth-gated, not UA-gated)."""
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)

    resp = internal.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": _BOT_UA},
        json={"url": "https://example.com/x"},
    )

    assert resp.status_code == 200
    assert "x-robots-tag" not in {k.lower() for k in resp.headers}
