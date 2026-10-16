"""`DocumentService`: what publishing does in what order, what a media code may
resolve to, and who a room names (ADR 0020).

The same-room rule is tested from both sides: a photo in room A is unknown in room B,
and a code that fits two photos is refused rather than picked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest

from nora.app import documents_service as service_module
from nora.app.documents_service import DocumentService, rooms_named
from nora.documents import catalog_prompt
from nora.documents.check import Checked, Finding
from nora.domain.documents import RevisionIn, RevisionOutcome
from nora.domain.media import MediaObject
from nora.policy import Conversation, Policy
from nora.testing import FakeDocumentStore, FakeMediaObjectStore

ROOM = ("signal", "room-1")
OTHER = ("signal", "room-2")
PHOTO = "a3f2c1" + "0" * 58  # an inbound photo in room-1
PHOTO_TWIN = "a3f2c1" + "f" * 58  # shares the 6-hex handle with PHOTO
DRAWING = "9b8c7d" + "1" * 58  # an image Nora generated in room-1


NOW = 1_800_000_000_000
DAY = 86_400_000


@dataclass
class _Row:
    id: int
    ts: int
    body: str
    media_json: str | None


def _blob(sha: str, media_type: str = "image/jpeg", role: str = "") -> dict[str, Any]:
    blob: dict[str, Any] = {"sha256": sha, "media_type": media_type}
    if role:
        blob["role"] = role
    return blob


class _Transcript:
    """`recent` and `media_rows`, the two reads `room_sightings` makes. `rooms` are the
    newest rows of a room (what `recent` returns); `history` are older media rows that only
    `media_rows` reaches, as (days_ago, blob); `filler` pads `media_rows` with that many
    rows that match nothing, to hit the row cap."""

    def __init__(
        self,
        rooms: dict[str, list[list[str]]],
        *,
        fail: bool = False,
        history: dict[str, list[tuple[int, dict[str, Any]]]] | None = None,
        filler: int = 0,
    ) -> None:
        self.rooms = rooms
        self.fail = fail
        self.history = history or {}
        self.filler = filler

    async def recent(self, *, conversation_id: str, limit: int) -> list[_Row]:
        if self.fail:
            raise RuntimeError("db down")
        rows = [
            _Row(n, NOW - n, "", json.dumps([_blob(sha) for sha in shas]))
            for n, shas in enumerate(self.rooms.get(conversation_id, []), start=1)
        ]
        return rows[-limit:]

    async def media_rows(self, *, conversation_id: str, since_ts: int, limit: int) -> list[_Row]:
        if self.fail:
            raise RuntimeError("db down")
        rows = [
            _Row(10_000 + n, NOW - days * DAY, "", json.dumps([blob]))
            for n, (days, blob) in enumerate(self.history.get(conversation_id, []))
            if NOW - days * DAY >= since_ts
        ]
        if self.filler:
            rows += [
                _Row(50_000 + n, NOW - 1000 - n, "", json.dumps([_blob(f"{n:064x}")]))
                for n in range(self.filler)
            ]
        rows.sort(key=lambda r: (r.ts, r.id), reverse=True)
        return rows[:limit]


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
    history: dict[str, list[tuple[int, dict[str, Any]]]] | None = None,
    filler: int = 0,
    public_url: str = "",
    new_id: Callable[[], UUID] = uuid4,
    places: Any = None,
) -> tuple[DocumentService, FakeDocumentStore]:
    store = store or FakeDocumentStore()
    svc = DocumentService(
        store=store,
        records=_Transcript(rooms or {}, fail=fail_rows, history=history, filler=filler),  # type: ignore[arg-type]
        media=FakeMediaObjectStore(objs, fail=fail_media),
        media_window=50,
        room_ids=lambda platform, cid: (
            frozenset({"room-1", "room-1-alias"})
            if cid in {"room-1", "room-1-alias"}
            else frozenset({cid})
        ),
        is_person=lambda person_id: person_id in {"ken", "mei"},
        now_ms=lambda: NOW,
        public_url=public_url,
        new_id=new_id,
        place_locator=places,
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
async def test_a_good_document_is_stored_and_answered_with_no_link_when_no_origin_is_set() -> None:
    svc, store = _service()
    result = await _publish(svc, _doc({"type": "Markdown", "props": {"text": "hi"}}))
    assert result.status == "ok" and result.revision == 1
    assert result.url == ""  # no `public_url`: no path, no host, nothing to paste
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


async def test_a_picture_born_in_this_room_resolves_whatever_its_source() -> None:
    # The room match is the security property, not the source (the sandbox session's
    # `resolve_room_media`, used as is): a fetched picture born here is this room's.
    fetched = _object(
        DRAWING, source="fetched", provenance={"platform": "signal", "conversation_id": "room-1"}
    )
    svc, _ = _service(objs=(fetched,))
    assert (await _publish(svc, _doc(_image("9b8c7d")))).status == "ok"


async def test_a_room_that_cannot_be_read_is_unavailable_never_a_guess() -> None:
    svc, store = _service(rooms={"room-1": [[PHOTO]]}, fail_rows=True)
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "unavailable"
    assert (
        await store.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    ).total == 0
    down, dstore = _service(rooms={"room-1": [[PHOTO]]}, fail_media=False)
    down._media = FakeMediaObjectStore(fail=True)
    # An index that cannot be read is "could not check", never "no such picture", and
    # never a reason to trust the room's rows alone: nothing is saved.
    assert (await _publish(down, _doc(_image("a3f2c1")))).status == "unavailable"
    assert (
        await dstore.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    ).total == 0


async def test_a_photo_older_than_the_window_but_inside_90_days_resolves() -> None:
    svc, store = _service(history={"room-1": [(40, _blob(PHOTO))]})
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "ok"
    stored = await store.get(document_id=result.document_id)
    assert stored.spec["root"]["children"][0]["props"]["media"] == PHOTO


async def test_a_photo_older_than_90_days_is_not_found() -> None:
    svc, _ = _service(history={"room-1": [(91, _blob(PHOTO))]})
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "refused"
    assert result.findings[0].rule == "media_unknown"


async def test_a_truncated_scan_that_finds_nothing_is_unavailable_not_unknown() -> None:
    svc, _ = _service(filler=1001)
    result = await _publish(svc, _doc(_image("a3f2c1")))
    assert result.status == "unavailable"


async def test_a_truncated_scan_that_found_the_code_still_resolves() -> None:
    svc, _ = _service(filler=1001, history={"room-1": [(0, _blob(PHOTO))]})
    assert (await _publish(svc, _doc(_image("a3f2c1")))).status == "ok"


async def test_only_pictures_are_documents_material() -> None:
    clip = _blob(PHOTO, "video/mp4")
    cover = _blob(PHOTO_TWIN, "image/jpeg", role="cover")
    svc, _ = _service(history={"room-1": [(3, clip), (4, cover)]})
    assert (await _publish(svc, _doc(_image("a3f2c1")))).findings[0].rule == "media_unknown"


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
    assert read.url == ""


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
    # Without it a stale edit would silently replace newer work: a model's revision of v1 would be
    # stored as revision 3 over someone else's revision 2.
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
    # A media_json entry that is not a 64-hex content address never resolves, so it is refused
    # (`media_unknown`) rather than rewritten into the document.
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


async def test_with_a_public_url_the_link_is_the_origin_and_the_handle_never_the_uuid() -> None:
    svc, _ = _service(public_url="https://nora.example/")  # a trailing slash is dropped
    made = await _publish(svc, _doc(title="v1"))
    handle = made.document_id.hex[:12]
    assert made.url == f"https://nora.example/documents/{handle}"
    assert str(made.document_id) not in made.url and made.document_id.hex not in made.url
    read = await svc.read(
        platform=ROOM[0], conversation_id=ROOM[1], document_id=made.document_id
    )
    assert read.url == made.url
    revised = await _publish(
        svc, _doc(title="v2"), document_id=made.document_id, base_revision=1
    )
    assert revised.url == made.url  # one document, one link, whatever its revision


async def test_a_new_document_never_takes_a_handle_its_room_already_reads() -> None:
    # Two documents sharing a 12-hex handle would 404 BOTH links for the room forever (a
    # clash is refused, never picked): the id is drawn again until it is clear of them.
    taken = UUID("0123456789ab" + "0" * 20)
    clash = UUID("0123456789ab" + "f" * 20)
    fresh = UUID("fedcba987654" + "1" * 20)
    draws = iter([clash, clash, fresh])
    svc, store = _service(store=None, new_id=lambda: next(draws))
    await store.create(
        document_id=taken, platform=ROOM[0], conversation_id=ROOM[1], created_by="ken",
        revision=RevisionIn(
            title="x", spec={"version": 1}, text="x", sha256="0" * 64, media=(), written_by="t"
        ),
    )
    made = await _publish(svc, _doc(title="new"))
    assert made.status == "ok" and made.document_id == fresh


async def test_a_clash_in_another_room_is_none_of_this_rooms_business() -> None:
    # The guard looks at what THIS room can read; another room's document does not make
    # the link ambiguous for anyone here, so the first id drawn is kept.
    elsewhere = UUID("0123456789ab" + "0" * 20)
    first = UUID("0123456789ab" + "f" * 20)
    draws = iter([first, UUID("fedcba987654" + "1" * 20)])
    svc, store = _service(new_id=lambda: next(draws))
    await store.create(
        document_id=elsewhere, platform=OTHER[0], conversation_id=OTHER[1], created_by="mei",
        revision=RevisionIn(
            title="x", spec={"version": 1}, text="x", sha256="0" * 64, media=(), written_by="t"
        ),
    )
    made = await _publish(svc, _doc(title="new"))
    assert made.status == "ok" and made.document_id == first


# ------------------------------------------------------------------- lifecycle
async def _set(svc: DocumentService, doc: UUID, state: str, *, room=ROOM, person="ken"):
    return await svc.set_state(
        platform=room[0], conversation_id=room[1], person_id=person,
        document_id=doc, state=state,
    )


@pytest.mark.parametrize("state", ["archived", "deleted"])
async def test_a_revision_of_an_archived_or_deleted_document_is_refused_as_not_active(
    state: str,
) -> None:
    svc, store = _service()
    first = await _publish(svc, _doc(title="v1"))
    assert (await _set(svc, first.document_id, state)).status == "ok"
    result = await _publish(
        svc, _doc(title="v2"), document_id=first.document_id, base_revision=1
    )
    assert result.status == "refused"
    assert [f.rule for f in result.findings] == ["document_not_active"]
    assert state in result.findings[0].message
    assert len(await store.history(document_id=first.document_id)) == 1


async def test_a_store_that_reports_not_active_is_refused_not_stored() -> None:
    """The race: archived between the service's read and the store's write."""

    class _Archives(FakeDocumentStore):
        async def add_revision(self, **kw: Any) -> RevisionOutcome:
            return RevisionOutcome("not_active")

    svc, _ = _service(store=_Archives())
    first = await _publish(svc, _doc(title="v1"))
    result = await _publish(svc, _doc(title="v2"), document_id=first.document_id, base_revision=1)
    assert result.status == "refused"
    assert [f.rule for f in result.findings] == ["document_not_active"]


async def test_set_state_appends_and_restore_brings_a_deleted_document_back() -> None:
    svc, store = _service(public_url="https://nora.example")
    first = await _publish(svc, _doc(title="Trip"))
    gone = await _set(svc, first.document_id, "deleted")
    assert gone.status == "ok" and gone.changed and gone.previous == "active"
    assert gone.url == ""  # a deleted document's link opens nothing, so none is handed out
    # Deleted is out of every read a room makes, except the restore itself.
    read = await svc.read(platform=ROOM[0], conversation_id=ROOM[1], document_id=first.document_id)
    assert read.status == "refused" and read.findings[0].rule == "no_such_document"
    back = await _set(svc, first.document_id, "active", person="mei")
    assert back.status == "ok" and back.changed and back.previous == "deleted"
    assert back.url.startswith("https://nora.example/documents/")
    assert (await store.get(document_id=first.document_id)).summary.state == "active"
    again = await _set(svc, first.document_id, "active")
    assert again.status == "ok" and not again.changed
    assert store._states[first.document_id] == [("deleted", "ken"), ("active", "mei")]


async def test_another_rooms_document_cannot_have_its_state_changed() -> None:
    svc, store = _service()
    first = await _publish(svc, _doc(title="mine"), room=ROOM)
    other = await _set(svc, first.document_id, "deleted", room=OTHER)
    unknown = await _set(svc, uuid4(), "deleted", room=OTHER)
    assert other.status == "refused" and other == unknown
    assert (await store.get(document_id=first.document_id)).summary.state == "active"


async def test_only_a_known_person_changes_a_state_and_only_to_a_known_state() -> None:
    svc, store = _service()
    first = await _publish(svc, _doc(title="mine"))
    stranger = await _set(svc, first.document_id, "deleted", person="100200300")
    assert stranger.status == "refused" and stranger.findings[0].rule == "unknown_person"
    bad = await _set(svc, first.document_id, "purged")
    assert bad.status == "refused" and bad.findings[0].rule == "bad_state"
    assert (await store.get(document_id=first.document_id)).summary.state == "active"


async def test_a_store_that_is_down_changes_no_state_and_says_unavailable() -> None:
    class _Down(FakeDocumentStore):
        async def set_state(self, **kw: Any) -> bool:
            raise RuntimeError("db down")

    svc, _ = _service(store=_Down())
    first = await _publish(svc, _doc(title="mine"))
    assert (await _set(svc, first.document_id, "archived")).status == "unavailable"


async def test_an_archived_document_still_reads_and_says_it_is_archived() -> None:
    svc, _ = _service()
    first = await _publish(svc, _doc(title="mine"))
    await _set(svc, first.document_id, "archived")
    read = await svc.read(platform=ROOM[0], conversation_id=ROOM[1], document_id=first.document_id)
    assert read.status == "ok" and read.state == "archived"


@pytest.mark.parametrize("state", ["archived", "deleted"])
async def test_a_new_document_never_takes_the_handle_of_a_put_away_one(state: str) -> None:
    # A put-away document can be restored, and then two documents would share the handle
    # and both links would 404: the clash check sees documents in every state.
    taken = UUID("0123456789ab" + "0" * 20)
    clash = UUID("0123456789ab" + "f" * 20)
    fresh = UUID("fedcba987654" + "1" * 20)
    draws = iter([clash, fresh])
    svc, store = _service(store=None, new_id=lambda: next(draws))
    await store.create(
        document_id=taken, platform=ROOM[0], conversation_id=ROOM[1], created_by="ken",
        revision=RevisionIn(
            title="x", spec={"version": 1}, text="x", sha256="0" * 64, media=(), written_by="t"
        ),
    )
    await store.set_state(document_id=taken, state=state, set_by="ken")
    made = await _publish(svc, _doc(title="new"))
    assert made.status == "ok" and made.document_id == fresh


async def test_a_handle_shared_with_another_rooms_document_resolves_to_this_rooms() -> None:
    # Resolved among THIS room's documents only: another room's document with the same
    # handle neither wins nor makes the handle ambiguous here.
    mine = UUID("0123456789ab" + "1" * 20)
    theirs = UUID("0123456789ab" + "2" * 20)
    svc, store = _service()
    for doc, room in ((mine, ROOM), (theirs, OTHER)):
        await store.create(
            document_id=doc, platform=room[0], conversation_id=room[1], created_by="ken",
            revision=RevisionIn(
                title="x", spec={"version": 1}, text="x", sha256="0" * 64, media=(),
                written_by="t",
            ),
        )
    out = await svc.set_state(
        platform=ROOM[0], conversation_id=ROOM[1], person_id="ken",
        handle="0123456789ab", state="deleted",
    )
    assert out.status == "ok" and out.document_id == mine
    assert (await store.get(document_id=theirs)).summary.state == "active"


# ----------------------------------------------------------- a delete keeps its way back
async def test_a_document_a_person_deleted_by_hand_is_still_restorable() -> None:
    """A person's own delete keeps its way back."""
    svc, _ = _service()
    made = await _publish(svc, _doc(title="Mine"))
    assert (await _set(svc, made.document_id, "deleted")).status == "ok"
    assert (await _set(svc, made.document_id, "active")).status == "ok"


def test_the_catalog_is_the_components_the_limits_and_the_rules_for_writing_one() -> None:
    svc, _ = _service()
    text = svc.catalog()
    assert text == catalog_prompt()
    assert "Write a finished reference, not notes" in text and "`media_fetch`" in text
    assert "Page" in text and "Mermaid" in text, "the components follow the rules"


def test_the_service_says_which_origin_a_link_is_built_under() -> None:
    assert _service(public_url="https://nora.example/")[0].link_base == "https://nora.example"
    assert _service()[0].link_base == ""


# ---- Map: the model names places, the service looks them up ---------------


class _Places:
    """A locator that knows a few public places and records every name it was asked for."""

    KNOWN: ClassVar[dict[str, dict[str, Any]]] = {
        "Senso-ji": {"status": "ok", "name": "Senso-ji", "lat": 35.7148, "lng": 139.7967},
        "Fushimi Inari": {"status": "ok", "name": "Fushimi Inari", "lat": 34.9671, "lng": 135.7727},
        "Central": {"status": "ambiguous", "could_be": ["Central, HK", "Central, Tokyo"]},
    }

    def __init__(self, *, down: bool = False) -> None:
        self.asked: list[str] = []
        self.down = down

    async def locate(self, name: str) -> dict[str, Any]:
        self.asked.append(name)
        if self.down:
            raise RuntimeError("places are down")
        return dict(self.KNOWN.get(name, {"status": "not_found"}))


def _map(*places: dict[str, Any], **props: Any) -> dict[str, Any]:
    return {"type": "Map", "props": {"caption": "Where", "places": list(places), **props}}


async def test_a_map_place_is_looked_up_by_name_and_stored_with_its_coordinate() -> None:
    places = _Places()
    svc, store = _service(places=places)
    result = await _publish(svc, _doc(_map({"name": "Senso-ji"}, {"name": "Fushimi Inari"})))
    assert result.status == "ok", result
    stored = (await store.get(document_id=result.document_id)).spec
    got = stored["root"]["children"][0]["props"]["places"]
    assert got[0] == {"name": "Senso-ji", "lat": 35.7148, "lng": 139.7967}
    assert got[1]["lat"] == 34.9671


async def test_a_coordinate_or_route_the_model_wrote_is_discarded_never_kept() -> None:
    """DEC-24: whatever pair a spec carries is thrown away and looked up again, so a person's
    position taken from `where_is` cannot enter a document through a Map."""
    places = _Places()
    svc, store = _service(places=places)
    spec = _doc(_map({"name": "Senso-ji", "lat": 22.3, "lng": 114.2},
                     route=[{"lat": 22.3, "lng": 114.2}, {"lat": 22.4, "lng": 114.3}]))
    result = await _publish(svc, spec)
    assert result.status == "ok", result
    props = (await store.get(document_id=result.document_id)).spec["root"]["children"][0]["props"]
    assert props["places"][0]["lat"] == 35.7148 and "route" not in props


async def test_a_place_that_does_not_resolve_to_exactly_one_is_refused_and_named() -> None:
    svc, _ = _service(places=_Places())
    unknown = await _publish(svc, _doc(_map({"name": "Nowhereville"})))
    assert unknown.status == "refused"
    assert [f.rule for f in unknown.findings] == ["place_unknown"]
    ambiguous = await _publish(svc, _doc(_map({"name": "Central"})))
    assert [f.rule for f in ambiguous.findings] == ["place_ambiguous"]


async def test_a_person_a_coordinate_or_an_identifier_is_refused_before_any_lookup() -> None:
    places = _Places()
    svc, _ = _service(places=places)
    result = await _publish(
        svc, _doc(_map({"name": "Mei's home"}, {"name": "22.4106, 114.1245"})),
        person_names=frozenset({"mei"}),
    )
    assert result.status == "refused"
    assert [f.rule for f in result.findings] == ["place_refused", "place_refused"]
    assert places.asked == [], "a screened name must never leave the host"


async def test_a_down_locator_is_unavailable_never_a_stored_map_without_coordinates() -> None:
    for locator in (_Places(down=True), None):
        svc, _ = _service(places=locator)
        result = await _publish(svc, _doc(_map({"name": "Senso-ji"})))
        assert result.status == "unavailable", locator
        assert result.document_id is None, "nothing was stored"


async def test_a_document_with_no_map_never_asks_the_locator() -> None:
    places = _Places()
    svc, _ = _service(places=places)
    assert (await _publish(svc, _doc())).status == "ok"
    assert places.asked == []


async def test_a_name_used_twice_is_looked_up_once() -> None:
    places = _Places()
    svc, _ = _service(places=places)
    await _publish(svc, _doc(_map({"name": "Senso-ji"}), _map({"name": "Senso-ji"})))
    assert places.asked == ["Senso-ji"]


async def test_a_coordinate_written_with_a_space_instead_of_a_comma_is_refused_too() -> None:
    """Google Text Search locates a bare pair either way, so a pair such as `22.4106 114.1245` is
    refused as a place name with or without a comma."""
    places = _Places()
    svc, _ = _service(places=places)
    for name in ("22.4106 114.1245", "22.4106,114.1245", "-33.8 151.2"):
        result = await _publish(svc, _doc(_map({"name": name})))
        assert result.status == "refused", name
    assert places.asked == []


async def test_an_address_disclosed_this_turn_is_refused_before_any_lookup() -> None:
    places = _Places()
    svc, _ = _service(places=places)
    live = frozenset({"flat 5a, 12 nathan road, tsim sha tsui"})
    result = await _publish(
        svc, _doc(_map({"name": "Flat 5A, 12 Nathan Road, Tsim Sha Tsui"}, {"name": "Senso-ji"})),
        live_addresses=live,
    )
    assert result.status == "refused" and [f.rule for f in result.findings] == ["place_refused"]
    assert places.asked == ["Senso-ji"] or places.asked == [], places.asked


async def test_one_publish_may_look_up_only_fifty_different_places() -> None:
    places = _Places()
    svc, _ = _service(places=places)
    names = [{"name": f"Place {i}"} for i in range(26)]
    others = [{"name": f"Other {i}"} for i in range(26)]
    result = await _publish(svc, _doc(_map(*names), _map(*others)))
    assert result.status == "refused" and [f.rule for f in result.findings] == ["place_limit"]
    assert places.asked == [], "nothing was looked up, nothing was billed"


async def test_names_that_differ_only_in_case_or_spacing_are_one_lookup() -> None:
    places = _Places()
    svc, _ = _service(places=places)
    result = await _publish(
        svc, _doc(_map({"name": "Senso-ji"}, {"name": "senso-ji"}, {"name": "Senso-ji  "}))
    )
    assert result.status == "ok", result
    assert places.asked == ["Senso-ji"]


async def test_fifty_unknown_places_are_ten_findings_not_fifty() -> None:
    svc, _ = _service(places=_Places())
    result = await _publish(svc, _doc(_map(*[{"name": f"Nowhere {i}"} for i in range(40)])))
    assert result.status == "refused" and len(result.findings) == 10


async def test_a_stale_revision_is_answered_before_any_place_is_looked_up() -> None:
    """The store checks run before the place lookups, so «read it again and publish again» after a
    conflict does not pay for the same places twice."""
    places = _Places()
    svc, _ = _service(places=places)
    first = await _publish(svc, _doc(title="v1"))
    result = await _publish(
        svc, _doc(_map({"name": "Senso-ji"})), document_id=first.document_id, base_revision=99
    )
    assert result.status == "conflict"
    assert places.asked == []


async def test_coordinates_that_push_a_document_over_the_size_limit_are_a_fixable_refusal() -> None:
    """Grounding adds a pair per place, so a document that passes the first check could fail the
    second as «could not be re-checked», which she cannot fix and a retry pays for."""
    import json

    from nora.documents.check import MAX_BYTES

    places = _Places()
    places.KNOWN = {f"P{i}": {"status": "ok", "name": f"P{i}", "lat": 22.123456, "lng": 114.123456}
                    for i in range(50)}
    svc, _ = _service(places=places)

    # Five Markdown blocks of at most 20,000 characters hold the padding (3 bytes each).
    def sized(total: int) -> dict[str, Any]:
        blocks = [
            {"type": "Markdown", "props": {"text": "字" * min(20000, max(0, total - 20000 * i))}}
            for i in range(5)
        ]
        return _doc(*blocks, _map(*[{"name": f"P{i}"} for i in range(50)]))

    total = 20000 * 4
    spec = sized(total)
    for _ in range(200):
        if len(json.dumps(spec, ensure_ascii=False).encode()) >= MAX_BYTES - 700:
            break
        total += 200
        spec = sized(total)
    size = len(json.dumps(spec, ensure_ascii=False).encode())
    assert MAX_BYTES - 2500 < size <= MAX_BYTES, size
    result = await _publish(svc, spec)
    assert result.status == "refused" and result.findings[0].rule == "size", result
