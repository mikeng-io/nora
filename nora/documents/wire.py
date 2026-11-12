from dataclasses import dataclass
from typing import Any

from nora.agent.egress import scan_reply
from nora.app.cognition_wiring import context_screen as cognition_context_screen_fn
from nora.app.cognition_wiring import shared_document_room
from nora.app.startup import StartupError
from nora.documents.adapters.tools import build as _build
from nora.documents.adapters.tools import session_tool_specs as _session_tool_specs
from nora.domain.documents import DocumentStore
from nora.infra.sql.documents import SqlDocumentStore

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


def session_tool_specs(service, reader, authority):
    """A session's document tools, bound to its own capability (never a person)."""
    return _session_tool_specs(service, reader, authority)


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


def ground(allowlist, cfg, content_checks, doctrine_store, persons) -> GroundWiring:
    _require_a_sendable_public_url(cfg.documents.public_url, content_checks=content_checks)
    cognition_context_screen = cognition_context_screen_fn(
        content_checks=content_checks,
        doctrine_store=doctrine_store,
        canary=cfg.doctrine.canary,
    )
    cognition_shared_document_room = shared_document_room(allowlist=allowlist, persons=persons)
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
