"""R-100: a link to one of OUR documents is minted by a tool, never authored by the model.

Only `document_publish`, `document_read` and `document_set_status` return a URL,
so the reply is where the rule lives (`nora.agent.links.LinkCarry`): a document
link no tool handed her this turn is removed, and the link of a document
published this turn is added. Three levels: the carrier, the delivery path she
speaks through, and the tools that mint.
"""

from __future__ import annotations

from typing import Any

from nora.agent.links import UNSAVED_DOCUMENT_NOTE, LinkCarry, own_document_link_pattern
from nora.engagement.stages import deliver
from nora.obs.metrics import Metrics
from nora.testing import FakeRecordStore
from tests.unit.test_engagement_deliver import (
    BUDGETS,
    TARGET,
    TERMINAL,
    _doctrine,
    _FakeAdapter,
)

PUBLIC = "https://nora.example"
REAL = f"{PUBLIC}/documents/a85785958b14"
MADE_UP = f"{PUBLIC}/documents/8e2f4b1a9c00"


def _armed() -> LinkCarry:
    carrier = LinkCarry()
    carrier.claim_documents(PUBLIC)
    return carrier


# ---- the carrier ---------------------------------------------------------------------------------


def test_a_document_link_no_tool_handed_her_is_removed() -> None:
    text, added = _armed().apply(f"份 document 整咗：{MADE_UP}\n四張相入咗")
    assert "documents/" not in text and added == 0
    assert "份 document 整咗：" in text and "四張相入咗" in text, "her words stay"


def test_a_published_documents_link_is_added_and_a_made_up_one_beside_it_is_removed() -> None:
    carrier = _armed()
    carrier.mint_document(REAL, published=True)
    text, added = carrier.apply(f"搞掂：{MADE_UP}")
    assert MADE_UP not in text and text.endswith(REAL) and added == 1


def test_a_link_she_copied_correctly_is_kept_once() -> None:
    carrier = _armed()
    carrier.mint_document(REAL, published=True)
    text, added = carrier.apply(f"喺呢度：{REAL}")
    assert text.count(REAL) == 1 and added == 0


def test_the_link_is_added_even_when_the_reply_never_mentions_it() -> None:
    carrier = _armed()
    carrier.mint_document(REAL, published=True)
    text, added = carrier.apply("整好咗，要登入先睇到。")
    assert text.endswith("\n" + REAL) and added == 1


def test_a_document_only_read_this_turn_may_be_repeated_but_is_not_added() -> None:
    carrier = _armed()
    carrier.mint_document(REAL, published=False)
    assert carrier.apply("睇返先：" + REAL) == ("睇返先：" + REAL, 0)
    assert carrier.apply("冇提過份嘢") == ("冇提過份嘢", 0)


def test_nothing_published_means_no_link_to_add() -> None:
    """The case behind the rule: the publish never returned ok, so the room gets no link."""
    text, added = _armed().apply(f"份 document 整咗：{MADE_UP}")
    assert added == 0 and "documents/" not in text


def test_a_made_up_link_beside_cantonese_is_removed_too() -> None:
    """Python's `\\w` and `\\b` count a Chinese character as a word character, so a pattern built on
    them would let a link with no space beside Cantonese through."""
    for text in (
        f"份文件喺{MADE_UP}呢度",
        "份文件喺nora.example/documents/8e2f4b1a9c00 ok",
        "連結：nora.example/documents/8e2f4b1a9c00。",
    ):
        cleaned, _ = _armed().apply(text)
        assert "documents/" not in cleaned, text


def test_a_locale_prefixed_link_and_an_uppercase_one_are_removed() -> None:
    for text in (
        f"{PUBLIC}/zh-HK/documents/8e2f4b1a9c00",
        "HTTPS://NORA.EXAMPLE/documents/8E2F4B1A9C00",
    ):
        cleaned, _ = _armed().apply(text)
        assert "ocuments/" not in cleaned, text


def test_a_handle_that_only_starts_like_a_real_one_is_not_the_real_one() -> None:
    carrier = _armed()
    carrier.mint_document(REAL, published=False)
    cleaned, _ = carrier.apply(f"{REAL}00000000000000000000")  # a 32-digit run, real handle first
    assert "documents/" not in cleaned


def test_a_markdown_link_to_a_made_up_document_keeps_its_label() -> None:
    cleaned, _ = _armed().apply(f"[京都・大阪 5 日]({MADE_UP}) 喺度")
    assert cleaned == "京都・大阪 5 日 喺度"


def test_another_hosts_link_with_the_same_path_is_left_alone() -> None:
    other = "https://example.com/documents/8e2f4b1a9c00"
    assert _armed().apply(f"see {other}") == (f"see {other}", 0)


def test_the_rule_is_off_until_a_documents_tool_arms_it() -> None:
    carrier = LinkCarry()  # no `[documents]`: no origin, so no document link can exist
    assert carrier.apply(f"see {MADE_UP}") == (f"see {MADE_UP}", 0)


def test_a_made_up_link_is_counted_and_a_clean_reply_is_not() -> None:
    carrier = _armed()
    carrier.apply("nothing here")
    assert carrier.stripped == 0
    carrier.apply(MADE_UP)
    assert carrier.stripped == 1


def test_the_pattern_reads_the_origin_without_a_scheme_or_trailing_slash() -> None:
    assert own_document_link_pattern("https://nora.example/").search("nora.example/documents/a85785958b14")


# ---- the delivery path she speaks through ------------------------------------------------------


def _handle(adapter: _FakeAdapter, metrics: Metrics):
    return deliver.build(
        store=FakeRecordStore(), adapter=adapter, target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="t1", nora_sender_id="nora", conversation_id="g1",
        budgets=BUDGETS, terminal=TERMINAL, metrics=metrics, audience="tag", composer=None,
    )


async def test_a_reply_with_a_made_up_link_is_sent_without_it_and_counted() -> None:
    """Her final text, after a publish that failed. The room hears the
    rest of what she said, never the invented link, and it is counted."""
    adapter, metrics = _FakeAdapter(), Metrics()
    handle = _handle(adapter, metrics)
    handle.links.claim_documents(PUBLIC)
    assert await handle.deliverer.deliver(f"份 document 整咗：{MADE_UP}\n四張相入咗") is True
    (_, sent) = adapter.sent[0]
    assert "documents/" not in sent and "四張相入咗" in sent
    assert metrics.get("nora_reply_links_carried_total", outcome="stripped") == 1


async def test_a_published_documents_link_reaches_the_room_in_the_reply_she_wrote() -> None:
    adapter, metrics = _FakeAdapter(), Metrics()
    handle = _handle(adapter, metrics)
    handle.links.claim_documents(PUBLIC)
    handle.links.mint_document(REAL, published=True)
    assert await handle.deliverer.deliver("搞掂，要登入先睇到。") is True
    assert adapter.sent[0][1].endswith(REAL)
    assert metrics.get("nora_reply_links_carried_total", outcome="document_added") == 1
    assert metrics.get("nora_reply_links_carried_total", outcome="appended") == 0, (
        "the place-link counter is not moved by a document"
    )


async def test_a_reply_that_was_only_a_made_up_link_sends_nothing_and_says_why() -> None:
    """R-4 reads the draft, so a reply that was nothing but the link passed it; once the link is
    removed what is left is empty, and a blank message must not go out in its place."""
    adapter, metrics = _FakeAdapter(), Metrics()
    handle = _handle(adapter, metrics)
    handle.links.claim_documents(PUBLIC)
    assert await handle.deliverer.deliver(MADE_UP) is False
    assert adapter.sent == []
    assert handle.deliverer.last_outcome == "empty_reply"
    assert metrics.get("nora_silent_failures_total", reason="empty_reply") == 1


# ---- the tools that mint -----------------------------------------------------------------------


def _service(public_url: str = PUBLIC) -> Any:
    from nora.app.documents_service import DocumentService
    from nora.testing import FakeDocumentStore, FakeMediaObjectStore
    from tests.unit.test_document_tools import _Rows

    return DocumentService(
        store=FakeDocumentStore(), records=_Rows(), media=FakeMediaObjectStore((), fail=False),
        media_window=50, room_ids=lambda platform, cid: frozenset({cid}),
        is_person=lambda person_id: person_id == "miles", public_url=public_url,
    )


async def _tools_with(carrier: LinkCarry, service: Any = None) -> dict[str, Any]:
    from nora.agent.registry import ToolRegistry
    from nora.agent.room import Room
    from nora.testing import FakeMemoryStore, FakeRosterSource
    from tests.fakes import SinkSender
    from tests.unit.test_document_tools import MILES, ROOM, _Persons, _Rows

    room = Room(
        platform="signal", conversation_id=ROOM,
        roster_source=FakeRosterSource(groups={ROOM: (frozenset({"miles"}),)}),
        persons=_Persons(), tagger=MILES,
    )
    specs = ToolRegistry(documents=service or _service()).specs(
        store=_Rows(), conversation_id=ROOM, conversation_type="group", persons=_Persons(),
        honcho=FakeMemoryStore(), deliverer=SinkSender(), metrics=Metrics(), room=room,
        links=carrier,
    )
    return {s.name: s.call for s in specs}


def _doc() -> dict[str, Any]:
    from tests.unit.test_document_tools import _doc

    return _doc()


async def test_a_successful_publish_arms_the_carrier_and_mints_the_link() -> None:
    carrier = LinkCarry()
    tools = await _tools_with(carrier)
    out = await tools["document_publish"](spec=_doc())
    assert out["status"] == "ok" and out["url"].startswith(PUBLIC + "/documents/")
    text, added = carrier.apply("搞掂")
    assert text.endswith(out["url"]) and added == 1


async def test_a_publish_that_is_refused_mints_nothing_and_the_made_up_link_goes() -> None:
    """End to end: the publish is refused, she claims it anyway."""
    carrier = LinkCarry()
    tools = await _tools_with(carrier)
    out = await tools["document_publish"](spec='{"version": 1,}')  # not valid JSON
    assert out["status"] == "bad_arguments"
    text, added = carrier.apply(f"份 document 整咗：{MADE_UP}")
    assert added == 0 and "documents/" not in text


async def test_reading_a_document_lets_her_repeat_its_link_but_does_not_force_it() -> None:
    service = _service()
    made = await (await _tools_with(LinkCarry(), service))["document_publish"](spec=_doc())
    later = LinkCarry()  # a later turn: nothing published, nothing handed to her yet
    tools = await _tools_with(later, service)
    assert later.apply(made["url"]) == ("", 0), "a link she was not handed this turn is removed"
    read = await tools["document_read"](document_id=made["document_id"])
    assert read["status"] == "ok"
    assert later.apply("睇下 " + made["url"]) == ("睇下 " + made["url"], 0)
    assert later.apply("冇提過份嘢") == ("冇提過份嘢", 0), "a read adds nothing"


async def test_with_no_public_url_nothing_is_armed_and_no_link_is_touched() -> None:
    carrier = LinkCarry()
    tools = await _tools_with(carrier, _service(public_url=""))
    out = await tools["document_publish"](spec=_doc())
    assert out["status"] == "ok" and "url" not in out
    assert carrier.apply(f"see {MADE_UP}") == (f"see {MADE_UP}", 0)


async def test_the_link_goes_with_one_message_not_every_message_of_the_turn() -> None:
    """The note is not appended to every message of a turn, the canned fallback
    included: a message that carried it settles it; the next one does not."""
    adapter = _FakeAdapter()
    handle = _handle(adapter, Metrics())
    handle.links.claim_documents(PUBLIC)
    handle.links.mint_document(REAL, published=True)
    assert await handle.deliverer.deliver("搞掂。") is True
    assert await handle.deliverer.deliver("要唔要加張地圖？") is True
    assert await handle.deliverer.deliver("唔好意思。", source="fallback") is True
    sent = [text for _, text in adapter.sent]
    assert [t.count("/documents/") for t in sent] == [1, 0, 0], sent


async def test_a_link_that_was_due_but_not_sent_stays_due_for_the_next_message() -> None:
    adapter = _FakeAdapter(succeed=False)
    handle = _handle(adapter, Metrics())
    handle.links.claim_documents(PUBLIC)
    handle.links.mint_document(REAL, published=True)
    assert await handle.deliverer.deliver("搞掂。") is False        # the send failed
    adapter._succeed = True
    assert await handle.deliverer.deliver("再試。") is True
    assert adapter.sent[-1][1].endswith(REAL)


async def test_the_recorded_copy_of_a_gif_caption_never_keeps_a_made_up_link() -> None:
    """`record_text` is built from the caption after the removal, so the audit
    row (what next turn's transcript and the notes read) never keeps a link the room never saw."""
    from nora.testing import FakeRecordStore

    store, adapter = FakeRecordStore(), _FakeAdapter()
    handle = deliver.build(
        store=store, adapter=adapter, target=TARGET, doctrine=_doctrine(), canary="C",
        tag_id="t1", nora_sender_id="nora", conversation_id="g1", budgets=BUDGETS,
        terminal=TERMINAL, metrics=Metrics(), audience="tag", composer=None,
    )
    handle.links.claim_documents(PUBLIC)
    await handle.deliverer.deliver(
        f"done {MADE_UP}", record_text=f"done {MADE_UP} [GIF: yay]",
    )
    (row,) = await store.recent(conversation_id="g1", limit=1)
    assert "documents/" not in row.body and "[GIF: yay]" in row.body


def test_the_variants_a_made_up_link_could_be_written_in_are_caught() -> None:
    for text in (
        "https://www.nora.example/documents/8e2f4b1a9c00",
        "nora.example:443/documents/8e2f4b1a9c00",
        "nora.example/zh-Hant-HK/documents/8e2f4b1a9c00",
    ):
        cleaned, _ = _armed().apply(text)
        assert "documents/" not in cleaned, text


def test_removing_a_link_does_not_reflow_the_rest_of_her_reply() -> None:
    """Only the blank lines the removal itself leaves are tidied; her own spacing is not touched."""
    cleaned, _ = _armed().apply(f"第一行  \n\n{MADE_UP}\n\n\n第二行")
    assert cleaned == "第一行  \n\n第二行"


# ---- a turn that reached for document_publish and saved nothing says so ----------------------


def test_a_turn_that_called_publish_and_saved_nothing_carries_the_note_once() -> None:
    carrier = LinkCarry()
    carrier.document_tried()
    text, added = carrier.apply("份 document 整好喇")
    assert text == f"份 document 整好喇\n\n{UNSAVED_DOCUMENT_NOTE}" and added == 0
    carrier.mark_sent(text)
    assert carrier.apply("仲等你答") == ("仲等你答", 0), "the next message is left alone"


def test_the_note_is_not_added_when_a_document_was_saved_or_publish_was_never_called() -> None:
    saved = LinkCarry()
    saved.document_tried()
    saved.document_saved()
    assert saved.apply("搞掂") == ("搞掂", 0)
    assert LinkCarry().apply("搞掂") == ("搞掂", 0)


def test_the_note_never_makes_an_empty_reply_sendable() -> None:
    carrier = LinkCarry()
    carrier.document_tried()
    assert carrier.apply("") == ("", 0)


def test_a_note_that_was_due_but_not_sent_stays_due_for_the_next_message() -> None:
    carrier = LinkCarry()
    carrier.document_tried()
    carrier.apply("搞掂")  # refused downstream: never reached the room
    assert carrier.apply("再講過")[0].count(UNSAVED_DOCUMENT_NOTE) == 1


async def test_reading_the_catalog_and_never_publishing_is_a_turn_that_saved_nothing() -> None:
    """The catalog was read, the spec went to her own
    computer, and the reply said it was made. With no public origin there is no link to strip,
    so the note is what the room is given."""
    carrier = LinkCarry()
    tools = await _tools_with(carrier, _service(public_url=""))
    assert (await tools["document_publish"]())["status"] == "bad_arguments"
    text, _ = carrier.apply("份嘢整好喇", final=True)
    assert text.endswith(UNSAVED_DOCUMENT_NOTE)


async def test_a_publish_that_stored_a_document_carries_no_note() -> None:
    for public_url in (PUBLIC, ""):
        carrier = LinkCarry()
        tools = await _tools_with(carrier, _service(public_url=public_url))
        assert (await tools["document_publish"](spec=_doc()))["status"] == "ok"
        assert UNSAVED_DOCUMENT_NOTE not in carrier.apply("搞掂")[0]


async def test_the_note_reaches_the_room_once_through_the_delivery_path() -> None:
    adapter = _FakeAdapter()
    handle = _handle(adapter, Metrics())
    handle.links.document_tried()
    assert await handle.deliverer.deliver("份嘢整好喇") is True
    assert await handle.deliverer.deliver("仲等你答") is True
    sent = [text for _, text in adapter.sent]
    assert [t.count(UNSAVED_DOCUMENT_NOTE) for t in sent] == [1, 0], sent


def test_a_progress_message_after_a_catalog_read_alone_is_not_marked_but_the_last_one_is() -> None:
    """«on it» sent between the catalog read and a good publish is not told
    «No document was saved». A catalog read alone marks only a message the turn ends on."""
    carrier = LinkCarry()
    carrier.document_called()
    assert carrier.apply("等我整") == ("等我整", 0)
    assert carrier.apply("整好喇", final=True)[0].endswith(UNSAVED_DOCUMENT_NOTE)


def test_a_publish_that_was_tried_and_refused_marks_any_message_after_it() -> None:
    carrier = LinkCarry()
    carrier.document_tried()
    assert carrier.apply("再試一次")[0].endswith(UNSAVED_DOCUMENT_NOTE)


async def test_only_a_message_the_turn_ends_on_follows_a_catalog_read_alone() -> None:
    adapter = _FakeAdapter()
    handle = _handle(adapter, Metrics())
    handle.links.document_called()
    assert await handle.deliverer.deliver("等我整") is True
    assert await handle.deliverer.deliver("整好喇", source="recovered") is True
    sent = [text for _, text in adapter.sent]
    assert [t.count(UNSAVED_DOCUMENT_NOTE) for t in sent] == [0, 1], sent
