"""`DocumentService`: what publishing does in what order, what a media code may
resolve to, and who a room names (ADR 0020, design/artifact §5, §16).

The same-room rule is tested from both sides: a photo in room A is unknown in room B,
and a code that fits two photos is refused rather than picked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from nora.app import documents_service as service_module
from nora.app.documents_service import DocumentService, rooms_named
from nora.documents.check import Checked, Finding
from nora.domain.documents import RevisionOutcome
from nora.domain.media import MediaObject
from nora.policy import Conversation, Policy
from nora.testing import FakeDocumentStore, FakeMediaObjectStore

ROOM = ("signal", "room-1")
OTHER = ("signal", "room-2")
PHOTO = "a3f2c1" + "0" * 58  # an inbound photo in room-1
PHOTO_TWIN = "a3f2c1" + "f" * 58  # shares the 6-hex handle with PHOTO
DRAWING = "9b8c7d" + "1" * 58  # an image Nora generated in room-1


@dataclass
class _Row:
    media_json: str | None


class _Transcript:
    """Only `recent`, the one read the service makes. Rows by room."""

    def __init__(self, rooms: dict[str, list[list[str]]], *, fail: bool = False) -> None:
        self.rooms = rooms
        self.fail = fail

    async def recent(self, *, conversation_id: str, limit: int) -> list[_Row]:
        if self.fail:
            raise RuntimeError("db down")
        rows = [
            _Row(json.dumps([{"sha256": sha, "media_type": "image/jpeg"} for sha in shas]))
            for shas in self.rooms.get(conversation_id, [])
        ]
        return rows[-limit:]


def _doc(*children: Any, title: str = "Saturday") -> dict[str, Any]:
    root = {"type": "Page", "props": {"title": title}, "children": list(children)}
    return {"version": 1, "root": root}


def _image(code: str) -> dict[str, Any]:
    return {"type": "Image", "props": {"media": code, "alt": "the door"}}


def _service(
    *,
    rooms: dict[str, list[list[str]]] | None = None,
    objs: tuple[MediaObject, ...] = (),
    store: FakeDocumentStore | None = None,
    fail_media: bool = False,
    fail_rows: bool = False,
) -> tuple[DocumentService, FakeDocumentStore]:
    store = store or FakeDocumentStore()
    svc = DocumentService(
        store=store,
        records=_Transcript(rooms or {}, fail=fail_rows),
        media=FakeMediaObjectStore(objs, fail=fail_media),
        media_window=50,
        room_ids=lambda platform, cid: (
            frozenset({"room-1", "room-1-alias"})
            if cid in {"room-1", "room-1-alias"}
            else frozenset({cid})
        ),
        is_person=lambda person_id: person_id in {"ken", "mei"},
    )
    return svc, store


def _object(sha: str, *, source: str, provenance: dict[str, Any] | None) -> MediaObject:
    return MediaObject(
        sha256=sha,
        media_type="image/png",
        size=1,
        source=source,
        description="d",
        provenance=provenance,
        created_at_ms=1,
    )


async def _publish(svc: DocumentService, raw: Any, *, room=ROOM, **kw: Any):
    return await svc.publish(
        platform=room[0],
        conversation_id=room[1],
        person_id="ken",
        raw=raw,
        written_by="tag:1",
        **kw,
    )


# ------------------------------------------------------------------- publishing
async def test_a_good_document_is_stored_and_answered_with_its_portal_path() -> None:
    svc, store = _service()
    result = await _publish(svc, _doc({"type": "Markdown", "props": {"text": "hi"}}))
    assert result.status == "ok" and result.revision == 1
    assert result.url == f"/documents/{result.document_id}"
    stored = await store.get(document_id=result.document_id)
    assert stored is not None
    assert stored.summary.title == "Saturday"
    assert stored.summary.created_by == "ken"
    assert (stored.summary.platform, stored.summary.conversation_id) == ROOM
    assert stored.written_by == "tag:1"
    assert len(stored.sha256) == 64 and "hi" in stored.text


async def test_a_refused_document_stores_nothing_and_carries_its_findings() -> None:
    svc, store = _service()
    result = await _publish(svc, _doc({"type": "Section", "props": {"className": "x"}}))
    assert result.status == "refused"
    assert result.findings and result.findings[0].rule == "additionalProperties"
    assert (
        await store.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    ).total == 0


async def test_a_checker_that_fails_is_unavailable_and_stores_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        service_module,
        "check_document",
        lambda raw: Checked(status="error", findings=(Finding("", "checker_error", "X"),)),
    )
    svc, store = _service()
    result = await _publish(svc, _doc())
    assert result.status == "unavailable" and "not saved" in result.note
    assert (
        await store.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    ).total == 0


async def test_a_store_that_is_down_is_unavailable_not_ok() -> None:
    class Down(FakeDocumentStore):
        async def create(self, **kwargs: Any):
            raise RuntimeError("db down")

    svc, _ = _service(store=Down())
    result = await _publish(svc, _doc())
    assert result.status == "unavailable" and "nothing was stored" in result.note


# ------------------------------------------------------------------------ media
async def test_an_inbound_photo_resolves_by_its_marker_code_and_is_stored_as_its_sha() -> None:
    svc, store = _service(rooms={"room-1": [[PHOTO]]})
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "ok", result
    stored = await store.get(document_id=result.document_id)
    assert stored is not None
    assert stored.media == (PHOTO,)
    assert stored.spec["root"]["children"][0]["props"]["media"] == PHOTO


async def test_a_photo_from_another_room_is_unknown_here() -> None:
    svc, store = _service(rooms={"room-2": [[PHOTO]]})
    result = await _publish(svc, _doc(_image("a3f2c1")), room=ROOM)
    assert result.status == "refused"
    assert [f.rule for f in result.findings] == ["media_unknown"]
    assert result.findings[0].path == "/root/children/0/props/media"
    assert (
        await store.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    ).total == 0
    # And the same code resolves in the room it was sent to.
    there, _ = _service(rooms={"room-2": [[PHOTO]]})
    assert (await _publish(there, _doc(_image("a3f2c1")), room=OTHER)).status == "ok"


async def test_a_handle_that_fits_two_pictures_is_refused_and_a_longer_code_is_not() -> None:
    svc, _ = _service(rooms={"room-1": [[PHOTO], [PHOTO_TWIN]]})
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "refused"
    assert [f.rule for f in result.findings] == ["media_ambiguous"]
    assert "use a longer code" in result.findings[0].message
    assert PHOTO[:8] in result.findings[0].message and PHOTO_TWIN[:8] in result.findings[0].message
    assert (await _publish(svc, _doc(_image("a3f2c10")))).status == "ok"
    assert (await _publish(svc, _doc(_image(PHOTO)))).status == "ok"


async def test_a_full_sha_outside_the_room_is_refused_too() -> None:
    svc, _ = _service(rooms={"room-2": [[PHOTO]]})
    assert (await _publish(svc, _doc(_image(PHOTO)))).status == "refused"


async def test_the_same_blob_in_several_rows_is_one_picture_not_an_ambiguity() -> None:
    svc, _ = _service(rooms={"room-1": [[PHOTO], [PHOTO], [PHOTO]]})
    assert (await _publish(svc, _doc(_image("a3f2c1"), _image("a3f2c1")))).status == "ok"


async def test_a_picture_born_in_this_room_resolves_and_one_born_elsewhere_does_not() -> None:
    mine = _object(
        DRAWING, source="generated", provenance={"platform": "signal", "conversation_id": "room-1"}
    )
    svc, store = _service(objs=(mine,))
    result = await _publish(svc, _doc(_image("9b8c7d")))
    assert result.status == "ok"
    assert (await store.get(document_id=result.document_id)).media == (DRAWING,)

    elsewhere = _object(
        DRAWING, source="generated", provenance={"platform": "signal", "conversation_id": "room-2"}
    )
    svc2, _ = _service(objs=(elsewhere,))
    assert (await _publish(svc2, _doc(_image("9b8c7d")))).status == "refused"


async def test_a_picture_with_no_room_on_it_never_resolves() -> None:
    # A sticker or a comprehension-born row carries no platform or conversation_id,
    # so its room cannot be told, and the rule fails closed.
    for provenance in (None, {}, {"platform": "signal"}, {"conversation_id": "room-1"}):
        svc, _ = _service(objs=(_object(DRAWING, source="inbound", provenance=provenance),))
        result = await _publish(svc, _doc(_image("9b8c7d")))
        assert result.status == "refused", provenance


async def test_a_source_the_rule_does_not_name_never_resolves() -> None:
    odd = _object(
        DRAWING, source="fetched", provenance={"platform": "signal", "conversation_id": "room-1"}
    )
    svc, _ = _service(objs=(odd,))
    assert (await _publish(svc, _doc(_image("9b8c7d")))).status == "refused"


async def test_a_room_that_cannot_be_read_is_unavailable_never_a_guess() -> None:
    svc, store = _service(rooms={"room-1": [[PHOTO]]}, fail_rows=True)
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "unavailable"
    assert (
        await store.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    ).total == 0
    down, _ = _service(rooms={"room-1": [[PHOTO]]}, fail_media=False)
    down._media = FakeMediaObjectStore(
        fail=True
    )  # the index answering empty is "nothing found", not down
    assert (
        await _publish(down, _doc(_image("a3f2c1")))
    ).status == "ok"  # the room's own rows still resolve it


async def test_what_is_stored_was_judged_and_its_hash_is_of_the_rewritten_form() -> None:
    svc, store = _service(rooms={"room-1": [[PHOTO]]})
    short = await _publish(svc, _doc(_image("a3f2c1")))
    longer = await _publish(svc, _doc(_image(PHOTO)))
    a = await store.get(document_id=short.document_id)
    b = await store.get(document_id=longer.document_id)
    assert a.sha256 == b.sha256  # one document, however the picture was named


# -------------------------------------------------------------------- revisions
async def test_a_revision_made_in_the_same_room_becomes_the_current_document() -> None:
    svc, store = _service()
    first = await _publish(svc, _doc(title="v1"))
    second = await _publish(svc, _doc(title="v2"), document_id=first.document_id, base_revision=1)
    assert (
        second.status == "ok" and second.revision == 2 and second.document_id == first.document_id
    )
    stored = await store.get(document_id=first.document_id)
    assert stored.summary.title == "v2" and stored.summary.created_by == "ken"
    assert len(await store.history(document_id=first.document_id)) == 2


async def test_a_room_cannot_revise_another_rooms_document() -> None:
    svc, store = _service()
    first = await _publish(svc, _doc(title="mine"), room=ROOM)
    result = await _publish(
        svc, _doc(title="taken"), room=OTHER, document_id=first.document_id, base_revision=1
    )
    assert result.status == "refused" and result.findings[0].rule == "no_such_document"
    # Indistinguishable from an id that is no document at all, so an id never confirms
    # that a document exists in another room.
    unknown = await _publish(
        svc, _doc(title="taken"), room=OTHER, document_id=uuid4(), base_revision=1
    )
    assert result.findings == unknown.findings and result.note == unknown.note
    assert (await store.get(document_id=first.document_id)).summary.title == "mine"


async def test_a_room_reads_its_own_document_at_the_current_revision() -> None:
    svc, _ = _service()
    first = await _publish(svc, _doc(title="v1"))
    await _publish(svc, _doc(title="v2"), document_id=first.document_id, base_revision=1)
    read = await svc.read(
        platform=ROOM[0], conversation_id="room-1-alias", document_id=first.document_id
    )
    assert read.status == "ok" and read.revision == 2 and read.title == "v2"
    assert read.spec is not None and read.spec["root"]["props"]["title"] == "v2"
    assert read.url == f"/documents/{first.document_id}"


async def test_another_rooms_document_reads_exactly_like_an_unknown_one() -> None:
    svc, _ = _service()
    first = await _publish(svc, _doc(title="mine"), room=ROOM)
    other = await svc.read(
        platform=OTHER[0], conversation_id=OTHER[1], document_id=first.document_id
    )
    unknown = await svc.read(platform=OTHER[0], conversation_id=OTHER[1], document_id=uuid4())
    assert other.status == "refused" and other.spec is None
    assert other == unknown


async def test_a_store_that_is_down_reads_unavailable_not_missing() -> None:
    class _Down(FakeDocumentStore):
        async def get(self, **kw: Any):
            raise RuntimeError("db down")

    svc, _ = _service(store=_Down())
    read = await svc.read(platform=ROOM[0], conversation_id=ROOM[1], document_id=uuid4())
    assert read.status == "unavailable" and read.spec is None


def test_the_catalog_is_the_generated_prompt_the_schema_came_with() -> None:
    svc, _ = _service()
    text = svc.catalog()
    assert "Page" in text and "Mermaid" in text and len(text) > 1000


async def test_revising_an_unknown_document_is_refused() -> None:
    svc, _ = _service()
    result = await _publish(svc, _doc(), document_id=uuid4(), base_revision=1)
    assert result.status == "refused" and result.findings[0].rule == "no_such_document"


async def test_a_revision_that_loses_a_race_is_a_conflict_not_an_overwrite() -> None:
    class Racing(FakeDocumentStore):
        async def add_revision(self, *, document_id, base_revision, revision):
            # Someone else revises between this writer's read and its write.
            await super().add_revision(
                document_id=document_id, base_revision=base_revision, revision=revision
            )
            return await super().add_revision(
                document_id=document_id, base_revision=base_revision, revision=revision
            )

    svc, store = _service(store=Racing())
    first = await _publish(svc, _doc(title="v1"))
    result = await _publish(svc, _doc(title="v2"), document_id=first.document_id, base_revision=1)
    assert result.status == "conflict" and "again" in result.note
    assert (await store.get(document_id=first.document_id)).summary.revision == 2


async def test_an_outcome_the_store_reports_as_not_found_is_refused() -> None:
    class Vanishing(FakeDocumentStore):
        async def add_revision(self, **kwargs: Any):
            return RevisionOutcome("not_found")

    svc, _ = _service(store=Vanishing())
    first = await _publish(svc, _doc())
    assert (
        await _publish(svc, _doc(), document_id=first.document_id, base_revision=1)
    ).status == "refused"


async def test_a_revision_must_say_which_revision_it_started_from() -> None:
    # Without it a stale edit silently replaced newer work: a model's revision of v1
    # was stored as revision 3 over someone else's revision 2 (review of PR 654).
    svc, store = _service()
    first = await _publish(svc, _doc(title="v1"))
    result = await _publish(svc, _doc(title="v2"), document_id=first.document_id)
    assert result.status == "refused"
    assert result.findings[0].rule == "base_revision_required"
    assert (await store.get(document_id=first.document_id)).summary.revision == 1


async def test_an_edit_of_an_old_revision_is_a_conflict_not_an_overwrite() -> None:
    svc, store = _service()
    first = await _publish(svc, _doc(title="v1"))
    await _publish(svc, _doc(title="A's v2"), document_id=first.document_id, base_revision=1)
    stale = await _publish(
        svc, _doc(title="B edit of v1"), document_id=first.document_id, base_revision=1
    )
    assert stale.status == "conflict"
    assert "revision 2" in stale.note and stale.revision == 2
    stored = await store.get(document_id=first.document_id)
    assert stored.summary.title == "A's v2" and stored.summary.revision == 2
    # Having read revision 2, the same edit goes through as revision 3.
    again = await _publish(
        svc, _doc(title="B edit of v2"), document_id=first.document_id, base_revision=2
    )
    assert again.status == "ok" and again.revision == 3


async def test_a_room_that_answers_to_two_ids_is_one_room() -> None:
    # `room-1` and `room-1-alias` are one room (Conversation.ids): a document made under
    # one is revisable from the other, and a photo sent under the alias resolves.
    svc, _ = _service(rooms={"room-1-alias": [[PHOTO]]})
    first = await _publish(svc, _doc(title="v1"), room=ROOM)
    there = await _publish(
        svc,
        _doc(title="v2"),
        room=("signal", "room-1-alias"),
        document_id=first.document_id,
        base_revision=1,
    )
    assert there.status == "ok"
    assert (await _publish(svc, _doc(_image("a3f2c1")), room=ROOM)).status == "ok"
    # An unrelated room is still not it.
    assert (
        await _publish(svc, _doc(), room=OTHER, document_id=first.document_id, base_revision=2)
    ).status == "refused"


async def test_only_a_known_person_can_publish() -> None:
    svc, store = _service()
    result = await svc.publish(
        platform="signal",
        conversation_id="room-1",
        person_id="100200300",
        raw=_doc(),
        written_by="tag:1",
    )
    assert result.status == "refused" and result.findings[0].rule == "unknown_person"
    page = await store.list_visible(person_id="100200300", rooms=frozenset(), limit=10, offset=0)
    assert page.total == 0


async def test_a_malformed_picture_entry_is_not_a_picture() -> None:
    # A media_json entry that is not a 64-hex content address never resolves: it used to
    # be rewritten into the document and then fail the second check as `unavailable`.
    svc, _ = _service(rooms={"room-1": [["abcdef/../../x"]]})
    result = await _publish(svc, _doc(_image("abcdef")))
    assert result.status == "refused"
    assert [f.rule for f in result.findings] == ["media_unknown"]


# ----------------------------------------------------------------- rooms_named
def _policy() -> Policy:
    return Policy(
        conversations=(
            Conversation(
                platform="signal",
                ids=frozenset({"room-1", "room-1-old"}),
                persons=frozenset({"ken", "mei"}),
            ),
            Conversation(platform="discord", ids=frozenset({"d-1"}), persons=frozenset({"ken"})),
            Conversation(platform="signal", ids=frozenset({"open"}), persons=None),
            Conversation(platform="signal", ids=frozenset({"room-3"}), persons=frozenset({"jo"})),
        )
    )


def test_rooms_named_lists_every_id_of_every_room_that_names_the_person() -> None:
    assert rooms_named(_policy(), "ken") == frozenset(
        {
            ("signal", "room-1"),
            ("signal", "room-1-old"),
            ("discord", "d-1"),
        }
    )
    assert rooms_named(_policy(), "mei") == frozenset(
        {("signal", "room-1"), ("signal", "room-1-old")}
    )


def test_a_room_that_admits_everyone_names_nobody() -> None:
    assert ("signal", "open") not in rooms_named(_policy(), "ken")
    assert rooms_named(_policy(), "nobody") == frozenset()


@pytest.mark.parametrize("person", ["", "KEN", "ken "])
def test_a_person_id_is_matched_exactly(person: str) -> None:
    assert rooms_named(_policy(), person) == frozenset()
