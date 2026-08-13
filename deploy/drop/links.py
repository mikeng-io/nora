"""`ValkeyLinkStore` — slug -> URL, backed by the dedicated `valkey` service
(docker-compose.yml; Nora Drop spec §3/§4). Replicates `shortener.js`'s own
mint semantics: random slug, retry on collision, plain `GET`/`SET` (no
TTL/expiry — link slugs are not artifacts and the spec names no retention
policy for them).

`SET key value NX` (set-if-absent) rather than `shortener.js`'s
get-then-put — Valkey gives an atomic compare-and-set for free where
Cloudflare KV's own API does not, so there is no reason to keep the Worker's
tiny TOCTOU window once a better primitive is one call away.
"""

from __future__ import annotations

from typing import Protocol

from redis.asyncio import Redis

from slugs import SLUG_LEN, random_slug

MAX_MINT_ATTEMPTS = 5


class LinkStoreUnavailableError(Exception):
    """valkey could not be reached / did not answer — an operator problem,
    never "no such slug"; see `ValkeyLinkStore.resolve`'s own note."""


class LinkStore(Protocol):
    """The shape both `ValkeyLinkStore` and the tests' in-memory fake share —
    documented here as a Protocol so a test double is checked structurally,
    the same convention `nora/domain/ports.py` uses across the repo, even
    though this app does not import that module (own dependency set)."""

    async def create(self, *, url: str) -> str: ...

    async def resolve(self, *, slug: str) -> str | None: ...


class ValkeyLinkStore:
    """Satisfies `LinkStore` against a real `redis.asyncio.Redis` connected to
    `valkey` (protocol-identical, spec §4 — no code-level cost to the swap)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(self, *, url: str) -> str:
        """Mint a fresh slug for `url`. Raises `LinkStoreUnavailableError` on a
        connection failure; raises `RuntimeError` if `MAX_MINT_ATTEMPTS`
        collisions happen in a row (a slug space of `len(ALPHABET)**SLUG_LEN`
        makes this astronomically unlikely, matching the Worker's own
        comment) — both are real refusals, never a silently wrong slug."""
        for _ in range(MAX_MINT_ATTEMPTS):
            slug = random_slug()
            try:
                created = await self._redis.set(slug, url, nx=True)
            except Exception as exc:
                raise LinkStoreUnavailableError(str(exc)) from exc
            if created:
                return slug
        raise RuntimeError(f"could not allocate a slug in {MAX_MINT_ATTEMPTS} attempts")

    async def resolve(self, *, slug: str) -> str | None:
        """The URL for `slug`, or None if there is no such slug. Raises
        `LinkStoreUnavailableError` on a connection failure — `None` must mean
        "no such slug", never "could not check", the same absence-vs-
        unavailable distinction `nora.domain.ports` draws throughout."""
        try:
            value = await self._redis.get(slug)
        except Exception as exc:
            raise LinkStoreUnavailableError(str(exc)) from exc
        return value.decode() if isinstance(value, bytes) else value


__all__ = ["SLUG_LEN", "LinkStore", "LinkStoreUnavailableError", "ValkeyLinkStore"]
