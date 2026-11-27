from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nora.agent.egress import LeakGuard, scan_reply
from nora.app.startup import StartupError
from nora.documents.adapters.tools import build as _build
from nora.domain.documents import DocumentStore
from nora.infra.sql.documents import SqlDocumentStore

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


@dataclass(frozen=True)
class StoresWiring:
    documents_store: Any


def stores(engine) -> StoresWiring:
    documents_store: DocumentStore = SqlDocumentStore(engine)
    return StoresWiring(documents_store=documents_store)


@dataclass(frozen=True)
class GroundWiring:
    cognition_context_screen: Any
    cognition_shared_document_room: Any


def context_screen(
    *, content_checks: Any, doctrine_store: Callable[[], Any], canary: Any,
) -> Callable[[str], str | None]:
    """The autonomous read's screen: the reply scan plus the doctrine canary.
    `doctrine_store` is a getter, because it is built after the screen is bound."""
    def screen(text: str) -> str | None:
        return scan_reply(text, content_checks=content_checks) or LeakGuard(
            doctrine_store().get(applies_to="cognition"), canary
        ).check(text)
    return screen


def shared_document_room(
    *, allowlist: Any, persons: Any, access: Any = None,
) -> Callable[[str, str, str], bool]:
    """A room the unit's own identified persons share, for document reads."""
    def shared(unit: str, platform: str, cid: str) -> bool:
        if access is not None and not access.is_file_backend:
            # Known-person containment needs the selected backend's async read; this is a
            # sync surface, so managed mode refuses rather than trusting a stale file roster.
            return False
        c = allowlist.conversation(platform, cid)
        return bool(c is not None and c.unit == unit and c.persons
                    and c.persons <= persons.by_person.keys())
    return shared


def ground(allowlist, cfg, content_checks, doctrine_store, persons, access=None) -> GroundWiring:
    _require_a_sendable_public_url(cfg.documents.public_url, content_checks=content_checks)
    cognition_context_screen = context_screen(
        content_checks=content_checks,
        doctrine_store=doctrine_store,
        canary=cfg.doctrine.canary,
    )
    cognition_shared_document_room = shared_document_room(
        allowlist=allowlist, persons=persons, access=access)
    return GroundWiring(
        cognition_context_screen=cognition_context_screen,
        cognition_shared_document_room=cognition_shared_document_room,
    )


def _require_a_sendable_public_url(public_url: str, *, content_checks: bool = True) -> None:
    """Refuse to start when egress would block the link she is told to send."""
    if not public_url:
        return
    link = f"{public_url}/documents/{'0' * 12}"
    kind = scan_reply(link, content_checks=content_checks)
    if kind is not None:
        raise StartupError(
            f"[documents] public_url = {public_url!r}: egress would block the link she "
            f"sends ({kind}) — refusing to start rather than silently dropping every reply "
            f"that carries a document link"
        )
