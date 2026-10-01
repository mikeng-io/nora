"""`DropArtifactIngest` — the Nora-side HTTP-client half of `ArtifactIngest`
(`nora.domain.ports`, Nora Drop spec §7a), calling `drop`'s own internal
`POST /api/artifacts` (`deploy/drop/artifacts.py` is the server side of this
same contract).

This is a plain sibling of `nora.infra.shortener.LinkShortener` — same
closure-over-base-url-and-token shape (DEC-8: the token is resolved by NAME
above this class, never read here) — with the one behavioural inversion
`ArtifactIngest.register`'s own docstring calls out: `LinkShortener.shorten`
is never load-bearing and answers `None` on any failure, but a write to
external custody either lands with a real id and URL or the caller must know
it did not — so this class RAISES (`ConnectionError`) on every failure shape,
mirroring `BlobStore.put`, not `LinkShortener`/`WeatherProvider`'s fail-soft
`None`. `nora.testing.FakeArtifactIngest.fail` exists to make exactly this
path testable by whatever future Compute tool calls through this port.

**Its caller is `computer_export`** (design/sandbox-runtime §6, S3a): the
composition root builds it on `[shortener]`'s Drop base URL and token when
`[computer] enabled`, and the tool publishes a file only after validating it
on Nora's side (size, sha256, a media type read off the bytes). Before
it this class existed so the `ArtifactIngest` Protocol had a real second
implementation (see `tests/contract/test_artifact_ingest_contract.py`).

**Multipart, not JSON, on the wire** — matching `deploy/drop/artifacts.py`'s
own `POST /api/artifacts` request shape exactly (see that module's docstring
for the base64-vs-multipart argument). `aiohttp.FormData` streams the file
field rather than buffering a base64-inflated JSON body, the same reasoning
`FilesystemBlobStore`'s docstring gives for treating a 100 MiB video as an
ordinary `bytes` value at this size class.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from nora.domain.ports import ArtifactRegistration

log = logging.getLogger("nora.drop")

__all__ = ["DropArtifactIngest"]


class DropArtifactIngest:
    """Satisfies `nora.domain.ports.ArtifactIngest`. Constructed once by a
    future Compute composition root, closing over `drop`'s internal
    listener's base URL (§5a: reachable only as `drop:<port>` on the `nora`
    Docker network, never a public host) and its bearer token."""

    def __init__(
        self, *, base_url: str, token: str, timeout_seconds: float = 30.0,
    ) -> None:
        self._register_url = base_url.rstrip("/") + "/api/artifacts"
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def register(
        self,
        *,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        """Register `data` with `drop`. RAISES `ConnectionError` on any
        failure — refused auth, a non-200, an unreachable listener, a body
        that is not the registered shape — never returns a sentinel, per this
        module's own docstring on why that differs from `LinkShortener`."""
        return await self._send(
            method="POST",
            url=self._register_url,
            title=title,
            media_type=media_type,
            expires_at=expires_at,
            data=data,
        )

    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        """Overwrite an existing artifact at `artifact_id`. Same raise-on-any-
        failure contract as `register` (this class's own docstring) — a 404
        for an unknown `artifact_id` is not distinguished from a 503 or a
        network failure; every non-200 raises `ConnectionError`, matching
        `register`'s own simplicity rather than inventing a richer error
        taxonomy this port doesn't otherwise have."""
        # `quote(..., safe="")` guards the one difference from `register`'s
        # fixed URL: `artifact_id` is caller-supplied and lands in the path,
        # so a value containing `/`, `?`, or other reserved characters must
        # not be allowed to reshape the request's target.
        update_url = f"{self._register_url}/{quote(artifact_id, safe='')}"
        return await self._send(
            method="PATCH",
            url=update_url,
            title=title,
            media_type=media_type,
            expires_at=expires_at,
            data=data,
        )

    async def _send(
        self,
        *,
        method: str,
        url: str,
        title: str,
        media_type: str,
        expires_at: int | None,
        data: bytes,
    ) -> ArtifactRegistration:
        """Shared wire mechanics for `register` (`POST`) and `update`
        (`PATCH`) — same multipart body, same auth header, same
        raise-on-any-failure contract; only the HTTP method and target URL
        differ between the two callers."""
        form = aiohttp.FormData()
        form.add_field("title", title)
        form.add_field("media_type", media_type)
        if expires_at is not None:
            form.add_field("expires_at", str(expires_at))
        form.add_field(
            "file", data, filename=title or "artifact", content_type=media_type,
        )
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    data=form,
                ) as response,
            ):
                if response.status != 200:
                    body_text = (await response.text())[:200]
                    raise ConnectionError(
                        f"drop refused {method} (http {response.status}): "
                        f"{body_text}"
                    )
                body: Any = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise  # shutdown is not a registration/update failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(
                f"drop unreachable ({type(exc).__name__})"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConnectionError(
                f"drop returned a non-JSON body ({type(exc).__name__})"
            ) from exc

        artifact_id = body.get("artifact_id") if isinstance(body, dict) else None
        returned_url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(artifact_id, str) or not artifact_id or not isinstance(
            returned_url, str,
        ) or not returned_url:
            raise ConnectionError(f"drop returned a malformed registration: {body!r}")
        return ArtifactRegistration(artifact_id=artifact_id, url=returned_url)
