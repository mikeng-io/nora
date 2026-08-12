"""The one slug alphabet, shared by mint (`POST /api/links`) and artifact
registration (`POST /api/artifacts`) — both need a short, dictation-safe
public path segment, and there is no reason for the two to invent different
ones.

Copied byte-for-byte from `deploy/shortener/worker/shortener.js`, the exact
contract task 3 is required to replicate: base58-ish, no `0/O/I/l`, so a slug
survives being read aloud.
"""

from __future__ import annotations

import secrets

ALPHABET = "123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
SLUG_LEN = 7


def random_slug() -> str:
    """A single random slug. Collision handling (retry N times) is the
    caller's job, exactly as it was the Worker's (`shortener.js`'s own
    `randomSlug` + retry loop) — this function only produces one candidate."""
    return "".join(secrets.choice(ALPHABET) for _ in range(SLUG_LEN))
