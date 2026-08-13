"""`PgRustfsArtifactStore` — the server side of `POST /api/artifacts`, the
concrete implementation of `nora.domain.ports.ArtifactIngest` (Nora Drop spec
§7a): bytes to RustFS, metadata to Postgres, one id that names both.

**Schema ownership.** This module never migrates the `artifacts` table — it is
owned by Nora's own alembic chain
(`nora/infra/sql/migrations/versions/0007_drop_artifacts.py`,
`nora/infra/sql/schema.py`'s `ARTIFACTS`), applied when `nora`/`presence`
start (or by hand from the host, `docker-compose.yml`'s comment on why `db`
publishes `127.0.0.1:5435`). This app only ever runs `SELECT`/`INSERT` against
a table it assumes already exists — see that migration's own docstring for why
one alembic chain owns a table two separate apps read/write.

**`id` doubles as the RustFS object key** — chosen once, before either the
bytes or the row exist, so the two can never name different objects. `slug` is
a SEPARATE public value (`slugs.py`'s alphabet), inserted with the same
collision-retry shape `links.py` uses for link slugs, so `POST /api/links` and
`POST /api/artifacts` share one mental model even though they write to two
different stores.

RustFS is called through `boto3` inside `asyncio.to_thread` — the same
blocking-I/O-off-the-event-loop pattern
`nora/infra/blobs/filesystem.py` uses for its own disk writes — rather than
pulling in `aiobotocore`'s larger dependency chain for a few-artifacts-a-day
service (this module's own `pyproject.toml` comment).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg
from botocore.exceptions import ClientError

from slugs import random_slug

MAX_SLUG_ATTEMPTS = 5


class ArtifactStoreUnavailableError(Exception):
    """Postgres or RustFS could not be reached, or refused the write for a
    reason that is not "the input was bad" — an operator problem, mapped to
    HTTP 503 by `server.py`, never confused with "no such artifact"."""


@dataclass(frozen=True)
class ArtifactRow:
    id: str
    slug: str
    title: str
    media_type: str
    size_bytes: int
    expires_at: int | None


@dataclass(frozen=True)
class Registration:
    artifact_id: str
    slug: str


class ArtifactStore(Protocol):
    """The shape both `PgRustfsArtifactStore` and the tests' in-memory fake
    share — see `links.py::LinkStore`'s docstring for why this is a Protocol
    even though this app does not depend on `nora.domain.ports`."""

    async def register(
        self, *, data: bytes, title: str, media_type: str, expires_at: int | None,
    ) -> Registration: ...

    async def update(
        self, *, artifact_id: str, data: bytes, title: str, media_type: str,
        expires_at: int | None = None,
    ) -> Registration | None: ...

    async def get_by_slug(self, *, slug: str) -> ArtifactRow | None: ...

    async def fetch_bytes(self, *, artifact_id: str) -> bytes | None: ...


class PgRustfsArtifactStore:
    """Satisfies `ArtifactStore` against a real `asyncpg.Pool` (the
    `artifacts` table) and a real `boto3` S3 client (RustFS)."""

    def __init__(self, *, pg_pool: asyncpg.Pool, s3_client: Any, bucket: str) -> None:
        self._pool = pg_pool
        self._s3 = s3_client
        self._bucket = bucket

    def ensure_bucket(self) -> None:
        """Idempotent bucket creation, called once at startup
        (`server.py`'s lifespan) — RustFS, like S3, has no implicit bucket."""
        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self._bucket)

    async def register(
        self,
        *,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> Registration:
        # Minted before either write, so bytes and row always agree on a name
        # (module docstring) — never derived from the row (which does not
        # exist yet) or the bytes (RustFS's own key IS this value, not a hash
        # of the content: two byte-identical artifacts registered separately
        # are two distinct artifacts with independent expiry, unlike
        # `FilesystemBlobStore`'s content-addressed dedup).
        artifact_id = f"art_{uuid.uuid4().hex}"
        try:
            await asyncio.to_thread(self._put_object, artifact_id, data, media_type)
        except Exception as exc:
            raise ArtifactStoreUnavailableError(f"rustfs put failed: {exc}") from exc

        async with self._pool.acquire() as conn:
            for _ in range(MAX_SLUG_ATTEMPTS):
                slug = random_slug()
                try:
                    await conn.execute(
                        "INSERT INTO artifacts "
                        "(id, slug, title, media_type, size_bytes, expires_at) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        artifact_id, slug, title, media_type, len(data), expires_at,
                    )
                except asyncpg.UniqueViolationError:
                    continue  # slug (rarely id) collision -- try another slug
                except Exception as exc:
                    raise ArtifactStoreUnavailableError(
                        f"postgres insert failed: {exc}"
                    ) from exc
                return Registration(artifact_id=artifact_id, slug=slug)
        raise ArtifactStoreUnavailableError(
            f"could not allocate a slug in {MAX_SLUG_ATTEMPTS} attempts"
        )

    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> Registration | None:
        """Overwrite an existing artifact's bytes/metadata AT ITS EXISTING id
        and slug — a targeted UPDATE, never a second INSERT (`register` mints
        a fresh id/slug pair every call; this reuses the one already assigned).

        Deliberately the REVERSE of `register`'s write order: Postgres first,
        RustFS second. Checking Postgres first means an update for an unknown
        `artifact_id` never touches RustFS at all — no orphaned object for a
        write that was going to 404 anyway. `expires_at=None` leaves the
        existing value alone (`COALESCE`), matching the same "None means no
        change" convention `ArtifactIngest.update`'s own docstring documents.

        Residual risk, named rather than solved (same posture Nora Drop spec
        §4 already takes toward RustFS's own maturity): if the Postgres UPDATE
        succeeds but the following RustFS put then fails, the row's metadata
        briefly describes bytes that haven't actually been overwritten yet.
        Not compensated here — artifacts are regenerable outputs, not a
        system of record, and a compensating transaction is more machinery
        than that failure mode's actual cost justifies.
        """
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "UPDATE artifacts SET title = $2, media_type = $3, "
                    "size_bytes = $4, expires_at = COALESCE($5, expires_at) "
                    "WHERE id = $1 RETURNING slug",
                    artifact_id, title, media_type, len(data), expires_at,
                )
            except Exception as exc:
                raise ArtifactStoreUnavailableError(f"postgres update failed: {exc}") from exc
        if row is None:
            return None

        try:
            await asyncio.to_thread(self._put_object, artifact_id, data, media_type)
        except Exception as exc:
            raise ArtifactStoreUnavailableError(f"rustfs put failed: {exc}") from exc
        return Registration(artifact_id=artifact_id, slug=row["slug"])

    def _put_object(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type,
        )

    async def get_by_slug(self, *, slug: str) -> ArtifactRow | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, slug, title, media_type, size_bytes, expires_at "
                    "FROM artifacts WHERE slug = $1",
                    slug,
                )
        except Exception as exc:
            raise ArtifactStoreUnavailableError(f"postgres select failed: {exc}") from exc
        if row is None:
            return None
        return ArtifactRow(**dict(row))

    async def fetch_bytes(self, *, artifact_id: str) -> bytes | None:
        try:
            return await asyncio.to_thread(self._get_object, artifact_id)
        except _NoSuchKeyError:
            return None
        except Exception as exc:
            raise ArtifactStoreUnavailableError(f"rustfs get failed: {exc}") from exc

    def _get_object(self, key: str) -> bytes:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
        except self._s3.exceptions.NoSuchKey as exc:
            raise _NoSuchKeyError from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise _NoSuchKeyError from exc
            raise
        return response["Body"].read()


class _NoSuchKeyError(Exception):
    """Internal-only: RustFS said "absent", not "unreachable" — the same
    distinction `ArtifactStoreUnavailableError`'s docstring draws, kept as a
    private sentinel so `fetch_bytes` can turn it into `None` in one place."""


__all__ = [
    "ArtifactRow",
    "ArtifactStore",
    "ArtifactStoreUnavailableError",
    "PgRustfsArtifactStore",
    "Registration",
]
