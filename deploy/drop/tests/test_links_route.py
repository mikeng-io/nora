"""`POST /api/links` — replicates `deploy/shortener/worker/shortener.js`'s
own contract exactly (Nora Drop spec §5): bearer-authed, `{"url": "https://…"}`
in, `{"slug", "short_url"}` out, https only.
"""

from __future__ import annotations

from conftest import TOKEN, FakeLinkStore
from starlette.testclient import TestClient


def test_a_mint_returns_a_slug_and_short_url(wired_apps):
    server, links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": "https://example.com/a/very/long/path"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"slug", "short_url"}
    assert body["short_url"] == f"https://nora.67.zone/i/{body['slug']}"
    assert links.created == ["https://example.com/a/very/long/path"]


def test_a_missing_token_is_403(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post("/api/links", json={"url": "https://example.com/x"})
    assert resp.status_code == 403


def test_a_wrong_token_is_403(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links",
        headers={"Authorization": "Bearer wrong"},
        json={"url": "https://example.com/x"},
    )
    assert resp.status_code == 403


def test_an_http_url_is_refused(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": "http://example.com/not-https"},
    )
    assert resp.status_code == 400


def test_a_malformed_https_url_is_refused(wired_apps):
    """A string-prefix check alone (`url.startswith("https://")`) would
    accept this — an embedded space with no real host — where
    `shortener.js`'s own `new URL(target).protocol` check would not.
    `urlsplit` closes that gap; this pins it."""
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": "https:// example.com/x"},
    )
    assert resp.status_code == 400


def test_a_non_json_body_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        content=b"not json",
    )
    assert resp.status_code == 400


def test_a_body_missing_url_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links", headers={"Authorization": f"Bearer {TOKEN}"}, json={},
    )
    assert resp.status_code == 400


def test_an_unavailable_link_store_is_503(wired_apps):
    server, _links, _artifacts = wired_apps
    server.internal_app.state.links = FakeLinkStore(fail=True)
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": "https://example.com/x"},
    )
    assert resp.status_code == 503


def test_the_public_redirect_uses_the_link_store(wired_apps):
    """The real public route (server.py's module docstring): mint via the
    internal listener, then resolve the same slug on the public one at
    `/i/<slug>` — the round trip this task's own verification checks end to
    end against real containers too."""
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": "https://example.com/target"},
    ).json()

    resp = public.get(f"/i/{minted['slug']}", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "https://example.com/target"


def test_an_unknown_slug_redirects_to_404(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/i/doesnotexist", follow_redirects=False)
    assert resp.status_code == 404


def test_a_slug_under_the_drop_prefix_does_not_resolve_as_a_redirect(wired_apps):
    """Path-based dispatch (server.py's module docstring): `/i/<slug>` and
    `/drop/<slug>` are disjoint prefixes on the one public host — a slug
    minted for a redirect must not resolve if fetched under the OTHER
    capability's prefix (there is no matching route for it at all under
    `/drop/`'s artifact-store lookup, so it 404s exactly like an unknown
    artifact slug would)."""
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/links",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"url": "https://example.com/target"},
    ).json()

    resp = public.get(f"/drop/{minted['slug']}")
    assert resp.status_code == 404
