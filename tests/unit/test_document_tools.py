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
    assert "verbatim" in out["note"] and "own line" in out["note"]
    assert "document_id" in out["note"]  # the rule: never write it in a reply
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
