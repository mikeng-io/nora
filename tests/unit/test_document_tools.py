"""`document_publish` and `document_read`: what a model is told, per outcome.

The tools are thin over `DocumentService` (its own tests cover the store, the
same-room rule and the checker). What is tested here is the translation: each
service outcome becomes one status the model can act on, a refusal carries the
checker's findings as plain sentences, and a turn nobody asked for publishes
nothing.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from uuid import UUID, uuid4

from nora.agent.egress import scan_identifiers, scan_reply
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.app.documents_service import DocumentService
from nora.domain.documents import handle_of
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeDocumentStore, FakeMediaObjectStore, FakeMemoryStore, FakeRosterSource
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
ROOM = "signal-room-1"
OTHER = "signal-room-2"


class _Rows:
    async def recent(self, *, conversation_id: str, limit: int) -> list[Any]:
        return []


class _Persons:
    by_person: ClassVar[dict[str, PersonRef]] = {"miles": MILES}

    def resolve(self, platform: str, sender_id: str):
        return None


def _service(
    store: FakeDocumentStore | None = None, *, public_url: str = ""
) -> DocumentService:
    return DocumentService(
        store=store or FakeDocumentStore(),
        records=_Rows(),
        media=FakeMediaObjectStore((), fail=False),
        media_window=50,
        room_ids=lambda platform, cid: frozenset({cid}),
        is_person=lambda person_id: person_id == "miles",
        public_url=public_url,
    )


def _tools(
    service: DocumentService | None,
    *,
    conversation_id: str = ROOM,
    tagger: PersonRef | None = MILES,
    fire_turn: bool = False,
    dream_adaptor: str = "",
    metrics: Metrics | None = None,
) -> dict:
    room = Room(
        platform="signal",
        conversation_id=conversation_id,
        roster_source=FakeRosterSource(groups={conversation_id: (frozenset({"miles"}),)}),
        persons=_Persons(),
        tagger=tagger,
    )
    specs = ToolRegistry(documents=service).specs(
        store=_Rows(),
        conversation_id=conversation_id,
        conversation_type="group",
        persons=_Persons(),
        honcho=FakeMemoryStore(),
        deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=room,
        fire_turn=fire_turn,
        dream_adaptor=dream_adaptor,
    )
    return {spec.name: spec.call for spec in specs}


def _doc(title: str = "December in Tokyo") -> dict[str, Any]:
    page = {
        "type": "Page",
        "props": {"title": title},
        "children": [
            {
                "type": "Section",
                "props": {"title": "Day 1"},
                "children": [{"type": "Markdown", "props": {"text": "Land, eat ramen."}}],
            }
        ],
    }
    return {"version": 1, "root": page}


async def test_a_good_spec_is_stored_and_answered_with_its_id_and_revision() -> None:
    store = FakeDocumentStore()
    out = await _tools(_service(store))["document_publish"](spec=_doc())
    assert out["status"] == "ok" and out["revision"] == 1
    stored = (
        await store.list_visible(person_id="miles", rooms=frozenset(), limit=10, offset=0)
    ).items
    assert [d.created_by for d in stored] == ["miles"]
    doc = await store.get(document_id=UUID(out["document_id"]))
    assert doc is not None and doc.written_by.startswith("tag:")


async def test_with_no_public_url_the_result_carries_no_link_and_says_so() -> None:
    # Empty `[documents] public_url`: no url, no path, no host, and the note tells her
    # plainly there is no link yet and where to find it (Documents, by title).
    out = await _tools(_service())["document_publish"](spec=_doc())
    assert out["status"] == "ok"
    assert "url" not in out
    note = out["note"]
    assert "no link to give yet" in note and "Documents" in note and "title" in note
    assert "http" not in note and "/documents/" not in note
    tools = _tools(_service())
    made = await tools["document_publish"](spec=_doc())
    seen = await tools["document_read"](document_id=made["document_id"])
    assert seen["status"] == "ok" and "url" not in seen


async def test_with_a_public_url_the_result_carries_the_handle_link_and_no_uuid() -> None:
    tools = _tools(_service(public_url="https://nora.example"))
    out = await tools["document_publish"](spec=_doc())
    handle = handle_of(UUID(out["document_id"]))
    assert out["url"] == f"https://nora.example/documents/{handle}"
    assert out["document_id"] not in out["url"]  # the link never carries the UUID
    assert "added to your reply for you" in out["note"]
    assert "Never write a link or the `document_id` yourself" in out["note"]
    read = await tools["document_read"](document_id=out["document_id"])
    assert read["url"] == out["url"]


def test_the_link_passes_egress_whole_and_the_uuid_does_not() -> None:
    # The floor stays: a UUID in a reply is blocked. The handle link is not a UUID, so the
    # whole sentence she would send passes the identifier scan and the reply scan.
    for _ in range(200):
        document_id = uuid4()
        link = f"https://nora.example/documents/{handle_of(document_id)}"
        reply = f"我整咗「星期六 Packing List」，喺度睇：\n{link}\n"
        assert scan_identifiers(reply) is None, link
        assert scan_reply(reply) is None, link
        assert scan_reply(f"id {document_id}") == "uuid"  # the control: egress is unchanged
    worst = "https://nora.67.zone/documents/" + "0123456789ab"
    assert scan_reply(worst) is None


async def test_a_spec_sent_as_json_text_is_accepted_too() -> None:
    out = await _tools(_service())["document_publish"](spec=json.dumps(_doc()))
    assert out["status"] == "ok"


async def test_no_spec_answers_with_the_catalog_and_stores_nothing() -> None:
    store = FakeDocumentStore()
    tools = _tools(_service(store))
    for empty in (None, "", "   "):
        out = await tools["document_publish"](spec=empty)
        assert out["status"] == "bad_arguments"
        assert "Page" in out["catalog"] and "Mermaid" in out["catalog"]
    page = await store.list_visible(person_id="miles", rooms=frozenset(), limit=10, offset=0)
    assert page.items == ()


async def test_a_refused_spec_carries_its_findings_as_sentences_and_stores_nothing() -> None:
    store = FakeDocumentStore()
    html = "<html><body><h1>Tokyo</h1></body></html>"
    out = await _tools(_service(store))["document_publish"](spec=html)
    assert out["status"] == "bad_arguments"
    assert out["findings"] and all(isinstance(f, str) and f for f in out["findings"])
    assert "not tell anybody" in out["note"]
    page = await store.list_visible(person_id="miles", rooms=frozenset(), limit=10, offset=0)
    assert page.items == ()


async def test_an_edit_needs_the_revision_read_and_a_stale_one_is_not_stored() -> None:
    tools = _tools(_service())
    first = await tools["document_publish"](spec=_doc("v1"))
    needs_base = await tools["document_publish"](spec=_doc("v2"), document_id=first["document_id"])
    assert needs_base["status"] == "bad_arguments"
    assert any("revision" in f for f in needs_base["findings"])
    edited = await tools["document_publish"](
        spec=_doc("v2"), document_id=first["document_id"], base_revision=1
    )
    assert edited["status"] == "ok" and edited["revision"] == 2
    stale = await tools["document_publish"](
        spec=_doc("v3"), document_id=first["document_id"], base_revision=1
    )
    assert stale["status"] == "bad_arguments" and stale["current_revision"] == 2
    assert (await tools["document_read"](document_id=first["document_id"]))["title"] == "v2"


async def test_another_rooms_document_is_not_found_for_a_revision_and_for_a_read() -> None:
    store = FakeDocumentStore()
    made = await _tools(_service(store), conversation_id=ROOM)["document_publish"](spec=_doc())
    there = _tools(_service(store), conversation_id=OTHER)
    revise = await there["document_publish"](
        spec=_doc("taken"), document_id=made["document_id"], base_revision=1
    )
    read = await there["document_read"](document_id=made["document_id"])
    unknown = await there["document_read"](document_id=str(uuid4()))
    assert revise["status"] == "not_found" and read["status"] == "not_found"
    assert read == unknown


async def test_a_turn_nobody_asked_for_publishes_nothing() -> None:
    for kwargs in ({"tagger": None}, {"fire_turn": True}, {"dream_adaptor": "proactive"}):
        store = FakeDocumentStore()
        out = await _tools(_service(store), **kwargs)["document_publish"](spec=_doc())
        assert out["status"] == "unavailable", kwargs
        page = await store.list_visible(person_id="miles", rooms=frozenset(), limit=10, offset=0)
        assert page.items == (), kwargs


async def test_a_value_that_is_not_an_id_is_named_not_guessed() -> None:
    tools = _tools(_service())
    bad = await tools["document_publish"](spec=_doc(), document_id="not-an-id", base_revision=1)
    read = await tools["document_read"](document_id="not-an-id")
    assert bad["status"] == "bad_arguments" and read["status"] == "not_found"


async def test_a_store_that_is_down_is_unavailable_never_ok_or_missing() -> None:
    class _Down(FakeDocumentStore):
        async def create(self, **kw: Any):
            raise RuntimeError("db down")

        async def get(self, **kw: Any):
            raise RuntimeError("db down")

    tools = _tools(_service(_Down()))
    assert (await tools["document_publish"](spec=_doc()))["status"] == "unavailable"
    assert (await tools["document_read"](document_id=str(uuid4())))["status"] == "unavailable"


async def test_an_unavailable_publish_tells_her_the_map_fallback() -> None:
    """A publish that could not be saved says so, and that nothing was stored; the Map
    fallback (name each place in Markdown) is part of the note."""
    class _Down(FakeDocumentStore):
        async def create(self, **kw: Any):
            raise RuntimeError("db down")

    out = await _tools(_service(_Down()))["document_publish"](spec=_doc())
    assert out["status"] == "unavailable"
    assert "each place named in Markdown text instead" in out["note"]


async def test_a_read_returns_the_spec_and_a_very_large_one_is_named_not_cut() -> None:
    tools = _tools(_service())
    made = await tools["document_publish"](spec=_doc())
    read = await tools["document_read"](document_id=made["document_id"])
    assert read["status"] == "ok" and read["revision"] == 1
    assert read["spec"]["root"]["props"]["title"] == "December in Tokyo"

    big = _doc()
    big["root"]["children"][0]["children"] = [
        {"type": "Markdown", "props": {"text": "x" * 15_000}} for _ in range(4)
    ]
    made_big = await tools["document_publish"](spec=big)
    assert made_big["status"] == "ok"
    read_big = await tools["document_read"](document_id=made_big["document_id"])
    assert read_big["status"] == "ok" and "spec" not in read_big
    assert read_big["revision"] == 1 and "too large" in read_big["note"]


async def test_every_outcome_is_counted_on_the_tool_metric() -> None:
    metrics = Metrics()
    tools = _tools(_service(), metrics=metrics)
    await tools["document_publish"](spec=_doc())
    await tools["document_publish"](spec=None)
    await tools["document_read"](document_id="nope")
    text = metrics.render() if hasattr(metrics, "render") else ""
    assert 'tool="document_publish"' in text and 'status="ok"' in text
    assert 'status="bad_arguments"' in text and 'tool="document_read"' in text


# ------------------------------------------------------------- document_set_status
async def test_set_status_archives_deletes_and_restores_through_the_service() -> None:
    store = FakeDocumentStore()
    tools = _tools(_service(store, public_url="https://nora.example"))
    made = await tools["document_publish"](spec=_doc("Trip"))
    doc = made["document_id"]
    archived = await tools["document_set_status"](document_id=doc, state="archived")
    assert archived["status"] == "ok" and archived["state"] == "archived"
    assert archived["previous"] == "active" and "url" not in archived
    # An archived document still reads, says so, and refuses an edit with a way out.
    seen = await tools["document_read"](document_id=doc)
    assert seen["status"] == "ok" and seen["state"] == "archived"
    edit = await tools["document_publish"](spec=_doc("v2"), document_id=doc, base_revision=1)
    assert edit["status"] == "bad_arguments" and "document_set_status" in edit["note"]
    deleted = await tools["document_set_status"](document_id=doc, state="deleted")
    assert deleted["status"] == "ok" and "Nothing was erased" in deleted["note"]
    assert (await tools["document_read"](document_id=doc))["status"] == "not_found"
    back = await tools["document_set_status"](document_id=doc, state="active")
    assert back["status"] == "ok" and back["previous"] == "deleted"
    assert back["url"].startswith("https://nora.example/documents/")
    again = await tools["document_set_status"](document_id=doc, state="active")
    assert again["status"] == "ok" and "already active" in again["note"]
    assert [s for s, _ in store._states[UUID(doc)]] == ["archived", "deleted", "active"]
    assert {by for _, by in store._states[UUID(doc)]} == {"miles"}
    # Her reply may carry what the result gives her; the result never carries the UUID
    # in a field she is told to repeat.
    assert "document_id" not in back["note"]


async def test_set_status_on_another_rooms_document_is_not_found_and_changes_nothing() -> None:
    store = FakeDocumentStore()
    made = await _tools(_service(store), conversation_id=ROOM)["document_publish"](spec=_doc())
    there = _tools(_service(store), conversation_id=OTHER)
    out = await there["document_set_status"](document_id=made["document_id"], state="deleted")
    unknown = await there["document_set_status"](document_id=str(uuid4()), state="deleted")
    assert out["status"] == unknown["status"] == "not_found"
    assert store._states == {}


async def test_a_turn_nobody_asked_for_changes_no_state() -> None:
    for kwargs in ({"tagger": None}, {"fire_turn": True}, {"dream_adaptor": "proactive"}):
        store = FakeDocumentStore()
        made = await _tools(_service(store))["document_publish"](spec=_doc())
        out = await _tools(_service(store), **kwargs)["document_set_status"](
            document_id=made["document_id"], state="deleted"
        )
        assert out["status"] == "unavailable", kwargs
        assert store._states == {}, kwargs


async def test_set_status_with_no_service_or_a_bad_id_or_a_down_store_changes_nothing() -> None:
    assert (await _tools(None)["document_set_status"](
        document_id=str(uuid4()), state="archived"))["status"] == "unavailable"
    assert (await _tools(_service())["document_set_status"](
        document_id="nope", state="archived"))["status"] == "not_found"

    class _Down(FakeDocumentStore):
        async def get(self, **kw: Any):
            raise RuntimeError("db down")

    out = await _tools(_service(_Down()))["document_set_status"](
        document_id=str(uuid4()), state="archived")
    assert out["status"] == "unavailable"


# ------------------------------------------- restore from what a later turn can see
async def _later_turn_block(store: FakeDocumentStore, conversation_id: str = ROOM) -> str:
    from datetime import UTC, datetime

    from nora.engagement.documents_context import DocumentsContextReader
    from nora.language.documents_render import render_documents

    reader = DocumentsContextReader(
        store=store, room_ids=lambda platform, cid: frozenset({cid}),
        clock=lambda: datetime(2026, 10, 2, tzinfo=UTC), metrics=Metrics(),
    )
    block = render_documents(await reader.read(platform="signal", conversation_id=conversation_id))
    return "" if block is None else block.body


def _id_on(body: str, prefix: str) -> str:
    import re

    (line,) = [row for row in body.splitlines() if row.startswith(prefix)]
    return re.search(r" · id ([0-9a-f-]{36}) · ", line).group(1)


async def test_a_deleted_document_is_restored_from_what_a_later_turn_sees() -> None:
    store = FakeDocumentStore()
    # Turn 1 publishes. Its result is deliberately dropped: no id survives from here.
    await _tools(_service(store))["document_publish"](spec=_doc("Packing list"))

    # Turn 2, «delete that packing list»: the id comes from <documents> only.
    body = await _later_turn_block(store)
    gone = await _tools(_service(store))["document_set_status"](
        document_id=_id_on(body, '"Packing list"'), state="deleted"
    )
    assert gone["status"] == "ok"

    # Turn 3, «bring it back»: the live list is empty, the put-away line carries the id.
    body = await _later_turn_block(store)
    assert not any(row.startswith('"') for row in body.splitlines())
    back = await _tools(_service(store))["document_set_status"](
        document_id=_id_on(body, 'deleted · "Packing list"'), state="active"
    )
    assert back["status"] == "ok" and back["previous"] == "deleted"
    assert (await _later_turn_block(store)).startswith('"Packing list"')


async def test_a_put_away_document_is_restored_by_the_handle_its_link_carries() -> None:
    store = FakeDocumentStore()
    tools = _tools(_service(store, public_url="https://nora.example"))
    made = await tools["document_publish"](spec=_doc("Trip"))
    handle = made["url"].rsplit("/", 1)[1]
    archived = await tools["document_set_status"](document_id=handle, state="archived")
    assert archived["status"] == "ok"
    back = await tools["document_set_status"](document_id=handle.upper(), state="active")
    assert back["status"] == "ok" and back["previous"] == "archived"


async def test_a_handle_from_another_room_or_no_document_is_not_found() -> None:
    store = FakeDocumentStore()
    made = await _tools(_service(store, public_url="https://nora.example"))["document_publish"](
        spec=_doc("Mine")
    )
    handle = made["url"].rsplit("/", 1)[1]
    there = _tools(_service(store), conversation_id=OTHER)
    other = await there["document_set_status"](document_id=handle, state="deleted")
    unknown = await there["document_set_status"](document_id="0" * 12, state="deleted")
    assert other["status"] == unknown["status"] == "not_found"
    assert other["note"] == unknown["note"]
    assert store._states == {}


def test_the_lifecycle_texts_never_route_a_remove_to_a_delete() -> None:
    """«remove that line» is an edit (`document_publish`). The tool's sent description and
    the skill's lifecycle paragraph say archive, delete, bring back: never «remove»."""
    import re
    from pathlib import Path

    described = dict(ToolRegistry(documents=_service())._descriptions())["document_set_status"]
    skill = Path("nora/skills/document-creation/SKILL.md").read_text(encoding="utf-8")
    para = skill.split("## Archive, delete or restore with the tool", 1)[1].split("\n## ", 1)[0]
    for text in (described, para):
        assert not re.search(r"\bremov", text, re.IGNORECASE)
    assert "edit, not a delete" in para


# ----------------------------------------------------- document_read by a link's handle
async def test_a_document_is_read_by_the_handle_its_link_carries_and_by_that_alone() -> None:
    store = FakeDocumentStore()
    tools = _tools(_service(store, public_url="https://nora.example"))
    made = await tools["document_publish"](spec=_doc("Trip"))
    handle = made["url"].rsplit("/", 1)[1]
    assert handle != made["document_id"] and len(handle) == 12

    # The handle and nothing else: the id is never passed.
    read = await tools["document_read"](document_id=handle)
    assert read["status"] == "ok" and read["title"] == "Trip" and read["revision"] == 1
    assert read["document_id"] == made["document_id"]
    assert read["spec"]["root"]["props"]["title"] == "Trip"
    # Pasted in capitals, as a link may be, it is the same document.
    assert (await tools["document_read"](document_id=handle.upper()))["revision"] == 1


async def test_a_handle_from_another_room_a_deleted_one_or_none_reads_as_an_unknown_id() -> None:
    store = FakeDocumentStore()
    here = _tools(_service(store, public_url="https://nora.example"))
    made = await here["document_publish"](spec=_doc("Mine"))
    handle = made["url"].rsplit("/", 1)[1]
    there = _tools(_service(store), conversation_id=OTHER)
    other = await there["document_read"](document_id=handle)
    unknown = await there["document_read"](document_id="0" * 12)
    assert other["status"] == unknown["status"] == "not_found" and other == unknown

    # Deleted in its own room, its link's handle reads like an unknown one too.
    await here["document_set_status"](document_id=handle, state="deleted")
    gone = await here["document_read"](document_id=handle)
    assert gone["status"] == "not_found" and gone == unknown
    # Not a handle at all (11 hex characters) is named, not looked up.
    assert (await here["document_read"](document_id="a" * 11))["status"] == "not_found"


async def test_a_handle_two_documents_of_the_room_share_is_refused_never_picked() -> None:
    from nora.domain.documents import RevisionIn

    store = FakeDocumentStore()
    first = UUID("abcdef012345" + "0" * 20)
    second = UUID("abcdef012345" + "1" * 20)
    for document_id in (first, second):
        await store.create(
            document_id=document_id, platform="signal", conversation_id=ROOM, created_by="miles",
            revision=RevisionIn(
                title="x", spec=_doc("x"), text="x", sha256="a" * 64, media=(), written_by="t:1"
            ),
        )
    out = await _tools(_service(store))["document_read"](document_id="abcdef012345")
    assert out["status"] == "not_found"


async def test_a_handle_another_room_shares_is_resolved_among_this_rooms_documents_only() -> None:
    from nora.domain.documents import RevisionIn

    store = FakeDocumentStore()
    mine = UUID("abcdef012345" + "0" * 20)
    theirs = UUID("abcdef012345" + "1" * 20)
    for document_id, room_id in ((mine, ROOM), (theirs, OTHER)):
        await store.create(
            document_id=document_id, platform="signal", conversation_id=room_id,
            created_by="miles",
            revision=RevisionIn(
                title="x", spec=_doc("x"), text="x", sha256="a" * 64, media=(), written_by="t:1"
            ),
        )
    out = await _tools(_service(store))["document_read"](document_id="abcdef012345")
    assert out["status"] == "ok" and out["document_id"] == str(mine)


async def test_a_spec_that_is_not_json_is_told_to_send_an_object_and_not_claim_it() -> None:
    """A long spec sent as text can break at one quote. The note names the fix and
    the claim, so she does not say the document was made when it was not."""
    broken = '{"version": 1, "root": {"type": "Page" "props": {"title": "T"}}}'
    out = await _tools(_service())["document_publish"](spec=broken)
    assert out["status"] == "bad_arguments"
    assert "not valid JSON" in out["note"] and "as an object" in out["note"]
    assert "do not tell anybody it was made" in out["note"]
    assert any("<<HERE>>" in f for f in out["findings"]), "the break is marked for her"


async def test_a_schema_refusal_keeps_its_own_note() -> None:
    bad = {"version": 1, "root": {"type": "Nonsense", "props": {}}}
    out = await _tools(_service())["document_publish"](spec=bad)
    assert out["status"] == "bad_arguments" and "not valid JSON" not in out["note"]
    assert "Fix each finding" in out["note"]


async def test_a_map_is_grounded_on_publish_and_the_model_never_reads_a_coordinate_back() -> None:
    """DEC-24: the stored Map carries the looked-up pair, but `document_read` hands the model
    the names alone, so a coordinate can neither be learned from nor copied out of a document."""

    class _Places:
        async def locate(self, name: str) -> dict[str, Any]:
            return {"status": "ok", "name": name, "lat": 35.7148, "lng": 139.7967}

    service = DocumentService(
        store=FakeDocumentStore(), records=_Rows(),  # type: ignore[arg-type]
        media=FakeMediaObjectStore((), fail=False), media_window=50,
        room_ids=lambda platform, cid: frozenset({cid}),
        is_person=lambda person_id: person_id == "miles", place_locator=_Places(),
    )
    tools = _tools(service)
    spec = _doc()
    spec["root"]["children"] = [{"type": "Map", "props": {
        "caption": "Where", "places": [{"name": "Senso-ji"}]}}]
    made = await tools["document_publish"](spec=spec)
    assert made["status"] == "ok", made
    stored = (await service._store.get(document_id=UUID(made["document_id"]))).spec
    assert stored["root"]["children"][0]["props"]["places"][0]["lat"] == 35.7148
    seen = await tools["document_read"](document_id=made["document_id"])
    place = seen["spec"]["root"]["children"][0]["props"]["places"][0]
    assert place == {"name": "Senso-ji"} and "35.71" not in json.dumps(seen)


def _map_service(asked: list[str]) -> DocumentService:
    class _Places:
        async def locate(self, name: str) -> dict[str, Any]:
            asked.append(name)
            return {"status": "ok", "name": name, "lat": 22.3, "lng": 114.2}

    return DocumentService(
        store=FakeDocumentStore(), records=_Rows(),  # type: ignore[arg-type]
        media=FakeMediaObjectStore((), fail=False), media_window=50,
        room_ids=lambda platform, cid: frozenset({cid}),
        is_person=lambda person_id: person_id == "miles", place_locator=_Places(),
    )


def _map_spec(*names: str) -> dict[str, Any]:
    spec = _doc()
    spec["root"]["children"] = [{"type": "Map", "props": {
        "caption": "Where", "places": [{"name": n} for n in names]}}]
    return spec


async def test_the_tool_hands_the_address_book_to_the_map_screen() -> None:
    """The tool passes the person names to the service
    (`person_names=_route_person_names`); dropping the argument would send a person's name to
    Google."""
    asked: list[str] = []
    out = await _tools(_map_service(asked))["document_publish"](spec=_map_spec(
        "Miles Novak's home"
    ))
    assert out["status"] == "bad_arguments", out
    assert any("cannot be mapped" in f for f in out["findings"]), out["findings"]
    assert asked == [], "a person's name must never leave the host"


async def test_an_address_reverse_geocode_gave_out_this_turn_cannot_be_pinned_on_a_map() -> None:
    """R-57: `route` refuses an address `reverse_geocode` disclosed this turn; a
    Map would have stored it as a pin. The tool passes the turn's set to the service."""
    asked: list[str] = []
    service = _map_service(asked)
    tools = _tools(service)
    # the closure's own set is what `reverse_geocode` fills; reach it the way the tool does
    from nora.agent import tools as tools_module

    assert "live_addresses" in tools_module.DocumentPublisher.publish.__annotations__
    out = await service.publish(
        platform="signal", conversation_id=ROOM, person_id="miles",
        raw=_map_spec("Flat 5A, 12 Nathan Road"), written_by="t",
        live_addresses=frozenset({"flat 5a, 12 nathan road"}),
    )
    assert out.status == "refused" and out.findings[0].rule == "place_refused"
    assert asked == [] and tools is not None
