"""The autonomous-reader half of the `DocumentStore` contract: authority
filtering, handle rechecks and the known-at cutoff — same two implementations
as `test_document_store_contract.py`."""

from __future__ import annotations

from nora.domain.documents import DocumentAuthority, handle_of
from tests.contract.test_document_store_contract import (
    OTHER_ROOM,
    ROOM,
    _make,
)
from tests.contract.test_document_store_contract import (
    store as store,
)


async def test_autonomous_authority_filters_before_limit_and_rechecks_handles(store):
    shared=await _make(store,created_by='ken',room=ROOM,title='Shared generated document')
    private=await _make(store,created_by='ken',room=OTHER_ROOM,title='Private generated document')
    grant=DocumentAuthority('u',frozenset({ROOM}),42)
    page=await store.list_visible(person_id=grant,rooms=frozenset({ROOM}),limit=1,offset=0)
    assert page.total==1 and [x.id for x in page.items]==[shared]
    assert (
        await store.get_visible(document_id=private, person_id=grant, rooms=frozenset({ROOM}))
        is None
    )
    assert await store.get_visible_by_handle(handle=handle_of(private),person_id=grant,
                                             rooms=frozenset({ROOM})) is None
    found=await store.get_visible_by_handle(handle=handle_of(shared),person_id=grant,
                                            rooms=frozenset({ROOM}))
    assert found is not None and found.summary.id==shared


async def test_autonomous_document_index_respects_known_at_cutoff(store):

    await _make(store, created_by="ken", room=ROOM, title="Generated known later")
    grant = DocumentAuthority("u", frozenset({ROOM}), 42, as_of_ms=0)
    hidden = await store.list_room(
        platform=ROOM[0], conversation_ids=frozenset({ROOM[1]}), limit=5,
        person_id=grant, rooms=frozenset({ROOM}),
    )
    current = await store.list_room(
        platform=ROOM[0], conversation_ids=frozenset({ROOM[1]}), limit=5,
        person_id=DocumentAuthority("u", frozenset({ROOM}), 42), rooms=frozenset({ROOM}),
    )
    assert hidden == () and len(current) == 1

