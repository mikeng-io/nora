"""In-memory fakes for `LinkStore`/`ArtifactStore`, and the shared bearer
token — used by every route test so this app's own suite never needs a real
valkey/rustfs/postgres (real ones are exercised by the separate,
docker-based verification step this task also ran by hand, not by
`uv run pytest` here).
"""

from __future__ import annotations

import pytest

from artifacts import ArtifactRow, ArtifactStoreUnavailableError, Registration
from links import LinkStoreUnavailableError

TOKEN = "test-drop-token"  # noqa: S105
JOB_SECRET = "test-job-secret"  # noqa: S105


class FakeLinkStore:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._urls: dict[str, str] = {}
        self._next = 1
        self.created: list[str] = []

    async def create(self, *, url: str) -> str:
        if self._fail:
            raise LinkStoreUnavailableError("valkey down")
        slug = f"slug{self._next:03d}"
        self._next += 1
        self._urls[slug] = url
        self.created.append(url)
        return slug

    async def resolve(self, *, slug: str) -> str | None:
        if self._fail:
            raise LinkStoreUnavailableError("valkey down")
        return self._urls.get(slug)


class FakeArtifactStore:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._rows: dict[str, ArtifactRow] = {}
        self._bytes: dict[str, bytes] = {}
        self._next = 1

    async def register(
        self, *, data: bytes, title: str, media_type: str, expires_at: int | None = None,
    ) -> Registration:
        if self._fail:
            raise ArtifactStoreUnavailableError("rustfs/postgres down")
        artifact_id = f"art_{self._next:06d}"
        slug = f"art{self._next:03d}"
        self._next += 1
        self._rows[slug] = ArtifactRow(
            id=artifact_id, slug=slug, title=title, media_type=media_type,
            size_bytes=len(data), expires_at=expires_at,
        )
        self._bytes[artifact_id] = data
        return Registration(artifact_id=artifact_id, slug=slug)

    async def get_by_slug(self, *, slug: str) -> ArtifactRow | None:
        if self._fail:
            raise ArtifactStoreUnavailableError("rustfs/postgres down")
        return self._rows.get(slug)

    async def fetch_bytes(self, *, artifact_id: str) -> bytes | None:
        if self._fail:
            raise ArtifactStoreUnavailableError("rustfs/postgres down")
        return self._bytes.get(artifact_id)

    async def update(
        self, *, artifact_id: str, data: bytes, title: str, media_type: str,
        expires_at: int | None = None,
    ) -> Registration | None:
        if self._fail:
            raise ArtifactStoreUnavailableError("rustfs/postgres down")
        slug = None
        for existing_slug, row in self._rows.items():
            if row.id == artifact_id:
                slug = existing_slug
                break
        if slug is None:
            return None
        new_expires_at = expires_at if expires_at is not None else self._rows[slug].expires_at
        self._rows[slug] = ArtifactRow(
            id=artifact_id, slug=slug, title=title, media_type=media_type,
            size_bytes=len(data), expires_at=new_expires_at,
        )
        self._bytes[artifact_id] = data
        return Registration(artifact_id=artifact_id, slug=slug)


@pytest.fixture
def wired_apps():
    """Wires fresh fakes onto BOTH module-level Starlette apps (they are
    singletons, per `server.py`'s own module docstring on why there are only
    two, not per-test instances) and hands the test the fakes so it can
    assert on them directly."""
    import server

    links = FakeLinkStore()
    artifacts = FakeArtifactStore()
    for app in (server.internal_app, server.public_app):
        app.state.token = TOKEN
        app.state.job_secret = JOB_SECRET
        app.state.links = links
        app.state.artifacts = artifacts
    return server, links, artifacts
