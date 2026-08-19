"""`POST /api/artifacts` — the HTTP implementation of `ArtifactIngest.
register`'s contract (`nora/domain/ports.py`, §7a): multipart in, `{artifact_id,
url}` out. `nora/infra/drop.py::DropArtifactIngest` (Nora's own repo) is the
client this shape has to match exactly — its own hermetic test suite
(`tests/test_drop_artifact_ingest.py`) sends the same request shape this
route parses.
"""

from __future__ import annotations

from conftest import JOB_SECRET, TOKEN, FakeArtifactStore
from starlette.testclient import TestClient

from artifacts import ArtifactRow
from auth import TokenScope, mint_job_token


def test_a_successful_register_returns_artifact_id_and_url(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "My Report", "media_type": "application/pdf"},
        files={"file": ("report.pdf", b"%PDF-1.4 fake bytes", "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"artifact_id", "url"}
    assert body["artifact_id"].startswith("art_")
    assert body["url"].startswith("https://nora.67.zone/drop/")


def test_a_successful_update_overwrites_title_and_media_type(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    minted = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Original", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v1", "text/plain")},
    ).json()
    artifact_id = minted["artifact_id"]

    resp = client.patch(
        f"/api/artifacts/{artifact_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Revised", "media_type": "text/markdown"},
        files={"file": ("t.md", b"v2", "text/markdown")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == artifact_id
    assert body["url"] == minted["url"]  # same slug, same URL -- the whole point


def test_an_update_actually_overwrites_the_bytes(wired_apps):
    server, _links, artifacts = wired_apps
    client = TestClient(server.internal_app)

    minted = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Original", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v1", "text/plain")},
    ).json()
    artifact_id = minted["artifact_id"]

    client.patch(
        f"/api/artifacts/{artifact_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Revised", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v2", "text/plain")},
    )

    slug = minted["url"].rsplit("/", 1)[-1]
    assert artifacts._bytes[artifact_id] == b"v2"
    assert artifacts._rows[slug].title == "Revised"


def test_an_update_without_expires_at_preserves_existing_value(wired_apps):
    server, _links, artifacts = wired_apps
    client = TestClient(server.internal_app)

    # Register with a specific expires_at value
    original_expires = 2_000_000_000_000  # some future epoch ms
    minted = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Original", "media_type": "text/plain", "expires_at": str(original_expires)},
        files={"file": ("t.txt", b"v1", "text/plain")},
    ).json()
    artifact_id = minted["artifact_id"]
    slug = minted["url"].rsplit("/", 1)[-1]

    # Update without providing expires_at (should preserve the original value)
    client.patch(
        f"/api/artifacts/{artifact_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "Revised", "media_type": "text/plain"},
        files={"file": ("t.txt", b"v2", "text/plain")},
    )

    # Verify the expires_at was preserved
    assert artifacts._rows[slug].expires_at == original_expires


def test_an_unknown_artifact_id_update_is_404(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_doesnotexist",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 404


def test_an_update_missing_the_token_is_403(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_000001",
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403


def test_an_update_missing_the_file_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_000001",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
    )
    assert resp.status_code == 400


def test_an_unavailable_store_on_update_is_503(wired_apps):
    server, _links, _artifacts = wired_apps
    server.internal_app.state.artifacts = FakeArtifactStore(fail=True)
    client = TestClient(server.internal_app)

    resp = client.patch(
        "/api/artifacts/art_000001",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 503


def test_expires_at_is_parsed_and_forwarded(wired_apps):
    server, _links, artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain", "expires_at": "1786320000000"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 200
    slug = resp.json()["url"].rsplit("/", 1)[-1]
    row = artifacts._rows[slug]
    assert row.expires_at == 1_786_320_000_000


def test_expires_at_absent_defaults_to_none(wired_apps):
    server, _links, artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    slug = resp.json()["url"].rsplit("/", 1)[-1]
    assert artifacts._rows[slug].expires_at is None


def test_a_missing_token_is_403(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 403


def test_a_missing_file_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
    )
    assert resp.status_code == 400


def test_a_missing_title_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 400


def test_a_bad_expires_at_is_400(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain", "expires_at": "not-a-number"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 400


def test_an_unavailable_artifact_store_is_503(wired_apps):
    server, _links, _artifacts = wired_apps
    server.internal_app.state.artifacts = FakeArtifactStore(fail=True)
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 503


def test_the_public_render_route_serves_the_bytes_back(wired_apps):
    """The real public render route (server.py's module docstring): register
    via the internal listener, `GET /drop/<slug>` on the public one gets the
    same bytes back with the stored content type."""
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"the actual bytes", "text/plain")},
    ).json()
    slug = minted["url"].rsplit("/", 1)[-1]

    resp = public.get(f"/drop/{slug}")
    assert resp.status_code == 200
    assert resp.content == b"the actual bytes"
    assert resp.headers["content-type"].startswith("text/plain")


def test_the_render_route_sets_an_inline_disposition_for_a_browser_native_type(wired_apps):
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "My Report", "media_type": "application/pdf"},
        files={"file": ("report.pdf", b"%PDF-1.4 fake bytes", "application/pdf")},
    ).json()
    slug = minted["url"].rsplit("/", 1)[-1]

    resp = public.get(f"/drop/{slug}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("inline;")
    assert 'filename="My Report.pdf"' in resp.headers["content-disposition"]
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_the_render_route_sets_an_attachment_disposition_for_an_unknown_type(wired_apps):
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "archive", "media_type": "application/zip"},
        files={"file": ("a.zip", b"PK\x03\x04", "application/zip")},
    ).json()
    slug = minted["url"].rsplit("/", 1)[-1]

    resp = public.get(f"/drop/{slug}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment;")


def test_an_unknown_artifact_slug_is_404(wired_apps):
    server, _links, _artifacts = wired_apps
    public = TestClient(server.public_app)

    resp = public.get("/drop/doesnotexist")
    assert resp.status_code == 404


def test_an_expired_artifact_is_410(wired_apps):
    server, _links, artifacts = wired_apps
    artifacts._rows["expired1"] = ArtifactRow(
        id="art_expired", slug="expired1", title="X", media_type="text/plain",
        size_bytes=1, expires_at=1,  # epoch ms 1 -- long past
    )
    artifacts._bytes["art_expired"] = b"x"
    public = TestClient(server.public_app)

    resp = public.get("/drop/expired1")
    assert resp.status_code == 410


def test_an_artifact_slug_under_the_redirect_prefix_is_404(wired_apps):
    """Path-based dispatch (server.py's module docstring): `/drop/<slug>` and
    `/i/<slug>` are disjoint prefixes on the one public host — an artifact
    slug fetched under the redirect capability's prefix must 404 (there is
    no matching link for it in the link store), not silently serve or
    redirect to the artifact anyway."""
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"the actual bytes", "text/plain")},
    ).json()
    slug = minted["url"].rsplit("/", 1)[-1]

    resp = public.get(f"/i/{slug}", follow_redirects=False)
    assert resp.status_code == 404


def test_an_old_style_unprefixed_path_is_404(wired_apps):
    """The retired `/d/<slug>` path (pre-cutover shape) is no longer a route
    at all — Starlette 404s a request that matches no route, same as an
    unknown slug under a real prefix."""
    server, _links, _artifacts = wired_apps
    internal = TestClient(server.internal_app)
    public = TestClient(server.public_app)

    minted = internal.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"the actual bytes", "text/plain")},
    ).json()
    slug = minted["url"].rsplit("/", 1)[-1]

    resp = public.get(f"/d/{slug}")
    assert resp.status_code == 404


def test_a_valid_create_scoped_token_may_post(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)
    scoped = mint_job_token(
        secret=JOB_SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60,
    )

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 200


def test_an_update_scoped_token_may_only_patch_its_own_artifact_id(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    created = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"hi", "text/plain")},
    ).json()
    scoped = mint_job_token(
        secret=JOB_SECRET, scope=TokenScope.UPDATE,
        artifact_id=created["artifact_id"], ttl_seconds=60,
    )

    ok = client.patch(
        f"/api/artifacts/{created['artifact_id']}",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T2", "media_type": "text/plain"},
        files={"file": ("t.txt", b"bye", "text/plain")},
    )
    assert ok.status_code == 200

    other_id_resp = client.patch(
        "/api/artifacts/art_someone_elses",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T2", "media_type": "text/plain"},
        files={"file": ("t.txt", b"bye", "text/plain")},
    )
    assert other_id_resp.status_code == 403


def test_a_create_scoped_token_may_not_patch(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)
    scoped = mint_job_token(
        secret=JOB_SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60,
    )

    resp = client.patch(
        "/api/artifacts/art_x",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403


def test_an_update_scoped_token_may_not_post(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)
    scoped = mint_job_token(
        secret=JOB_SECRET, scope=TokenScope.UPDATE, artifact_id="art_1", ttl_seconds=60,
    )

    resp = client.post(
        "/api/artifacts",
        headers={"Authorization": f"Bearer {scoped}"},
        data={"title": "T", "media_type": "text/plain"},
        files={"file": ("t.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403


def test_the_master_token_mints_a_scoped_token(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)

    resp = client.post(
        "/api/tokens",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"scope": "create", "ttl_seconds": 60},
    )
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_a_scoped_token_cannot_mint_another_token(wired_apps):
    server, _links, _artifacts = wired_apps
    client = TestClient(server.internal_app)
    scoped = mint_job_token(
        secret=JOB_SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60,
    )

    resp = client.post(
        "/api/tokens",
        headers={"Authorization": f"Bearer {scoped}"},
        json={"scope": "create", "ttl_seconds": 60},
    )
    assert resp.status_code == 403
