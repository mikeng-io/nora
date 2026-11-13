from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

from nora.agent.catalog.groups import LOAD_TRANSCRIPT_MAX
from nora.app.composition_helpers import _room_ids_of
from nora.app.documents_service import DocumentService, rooms_named
from nora.engagement.documents_context import DocumentsContextReader

"""Document service wiring steps."""


@dataclass(frozen=True)
class ServiceWiring:
    document_service: Any
    documents_context: Any


def service(
    allowlist: Any, audit: Any, cfg: Any, clock: Any,
    cognition_context_screen: Any, cognition_shared_document_room: Any, content_checks: Any,
    documents_store: Any, media_objects: Any, metrics: Any, persons: Any,
    route_client: Any,
) -> ServiceWiring:

    document_service = DocumentService(
        store=documents_store,
        records=audit,
        media=media_objects,
        media_window=cfg.store.context_window + LOAD_TRANSCRIPT_MAX,
        room_ids=_room_ids_of(allowlist),
        is_person=lambda person: person in persons.by_person,
        public_url=cfg.documents.public_url,
        # A Map names its places and the service looks them up. The same
        # client `route` and `place_info` use, so `[route]` off means a Map is `unavailable`.
        place_locator=route_client,
        # The read grant, from the same policy.toml the portal reads with (8765_44827).
        rooms_of=functools.partial(rooms_named, allowlist),
        content_checks=content_checks,
        shared_room=cognition_shared_document_room,
        autonomous_screen=cognition_context_screen,
    )
    # `<documents>`: the one reader the tag path and the fire path
    # share. `None` while `[documents]` is off, so no room renders the block (the same
    # flag that withholds the document tools).
    documents_context: DocumentsContextReader | None = None
    if cfg.documents.enabled:
        documents_context = DocumentsContextReader(
            store=documents_store,
            room_ids=_room_ids_of(allowlist),
            clock=clock,
            metrics=metrics,
            rooms_of=functools.partial(rooms_named, allowlist),
            shared_room=cognition_shared_document_room,
            autonomous_screen=cognition_context_screen,
        )
    return ServiceWiring(document_service=document_service, documents_context=documents_context)
