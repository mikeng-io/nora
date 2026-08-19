"""`server._legacy_host_redirect` — an already-shared `nora.example.net/i/<slug>`
or `/drop/<slug>` link must keep working forever after the public hostname
moves to `nora.67.zone`, redirecting rather than 404ing or serving directly
from the retired host."""

from __future__ import annotations

from starlette.testclient import TestClient


def test_a_redirect_link_hit_on_the_legacy_host_301s_to_the_new_host(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    # An unknown slug — proves the host check short-circuits BEFORE the link
    # store is ever consulted, not merely that a real slug happens to redirect.
    resp = public.get(
        "/i/doesnotexist", headers={"host": "nora.example.net"}, follow_redirects=False,
    )

    assert resp.status_code == 301
    assert resp.headers["location"] == "https://nora.67.zone/i/doesnotexist"


def test_a_drop_artifact_hit_on_the_legacy_host_301s_to_the_new_host(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get(
        "/drop/doesnotexist", headers={"host": "nora.example.net"}, follow_redirects=False,
    )

    assert resp.status_code == 301
    assert resp.headers["location"] == "https://nora.67.zone/drop/doesnotexist"


def test_a_request_on_the_current_host_is_never_redirected(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    # No legacy-host header at all (TestClient's own default) — must fall
    # through to the real 404, not the legacy redirect.
    resp = public.get("/i/doesnotexist", follow_redirects=False)

    assert resp.status_code == 404


def test_an_empty_legacy_hostname_disables_the_redirect_entirely(wired_apps, monkeypatch):
    """The module docstring's own documented off switch — a fresh deployment
    with no retired host to honor sets DROP_LEGACY_PUBLIC_HOSTNAME="" and
    every request just serves normally, even one that happens to carry a
    Host header matching what used to be the legacy value."""
    server, _links, _artifacts = wired_apps
    monkeypatch.setattr(server, "LEGACY_PUBLIC_HOSTNAME", "")
    public = TestClient(server.public_app)

    resp = public.get(
        "/i/doesnotexist", headers={"host": "nora.example.net"}, follow_redirects=False,
    )

    assert resp.status_code == 404
