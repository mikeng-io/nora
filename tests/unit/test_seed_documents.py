"""`seed_documents`: a case about a document she already made has to make it, the
production source holding none. Refused on a subject with
`[documents]` off, so the case cannot read as a model failure."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from nora.subject import seed_documents
from nora.testing import FakeDocumentStore

ID = "8d1f3a52-6c7e-4b9a-a1d4-5e2c9b7f0a31"


def _subject(*, enabled: bool):
    store = FakeDocumentStore()
    subject = SimpleNamespace(
        cfg=SimpleNamespace(documents=SimpleNamespace(enabled=enabled)),
        room=("signal", "g-eval"),
        deps=SimpleNamespace(documents=store),
    )
    return subject, store


async def test_a_named_document_is_made_in_the_subjects_room():
    subject, store = _subject(enabled=True)
    await seed_documents(
        subject, "case", [{"id": ID, "title": "Saturday dinner plan", "body": "Dinner at 7pm."}]
    )
    found = await store.list_room(
        platform="signal", conversation_ids=frozenset({"g-eval"}), limit=5,
        person_id="reader", rooms=frozenset({("signal", "g-eval")})
    )
    assert [(d.id, d.title, d.revision) for d in found] == [
        (UUID(ID), "Saturday dinner plan", 1)
    ]


async def test_a_subject_with_documents_off_is_refused_not_seeded():
    subject, store = _subject(enabled=False)
    with pytest.raises(ValueError, match=r"\[documents\] off"):
        await seed_documents(subject, "case", [{"id": ID, "title": "T", "body": "b"}])
    assert await store.list_room(
        platform="signal", conversation_ids=frozenset({"g-eval"}), limit=5,
        person_id="reader", rooms=frozenset({("signal", "g-eval")})
    ) == ()


async def test_no_documents_named_seeds_nothing_and_asks_nothing_of_the_config():
    subject, _ = _subject(enabled=False)
    await seed_documents(subject, "case", [])  # a case that seeds none runs on any subject
