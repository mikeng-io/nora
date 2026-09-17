"""GET /app/v1/doctrine — Nora's own current doctrine, read-only, for anyone
signed into the portal. Not person-scoped: the doctrine is the same for
everyone, same as `/app/v1/skills`.

Reuses the live `DoctrineStore` composition.py already wires for the tag
cycle itself, rather than opening a second copy — `.get(applies_to="tag")`
is the same call the tag path makes (nora/engagement/pipeline.py,
nora/app/fire.py), including its own mtime-based hot reload, so the portal
never drifts from what she is actually running on.

Only `name`, `description`, `version`, and `body` cross to the browser —
`DoctrineSection`'s `authority_above`/`authority_below`/`change_policy`/
`not_here`/`sha256`/`path` are the doctrine engine's own internal wiring,
not reader-facing.
"""

from __future__ import annotations

from typing import Any

from nora.doctrine.loader import DoctrineStore


def build_doctrine_payload(*, doctrine_store: DoctrineStore) -> list[dict[str, Any]]:
    doctrine = doctrine_store.get(applies_to="tag")
    return [
        {
            "name": section.name,
            "description": section.description,
            "version": section.version,
            "body": section.body.strip(),
        }
        for section in doctrine.sections
    ]
