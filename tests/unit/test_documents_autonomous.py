"""Autonomous and wildcard-room document access: who an authority names, what
a deferred session tool may do, and which revisions fail closed. Split from
`test_documents_service.py`."""

from __future__ import annotations

from uuid import uuid4

import pytest

from nora.app.documents_service import rooms_named
from nora.domain.documents import DocumentAuthority
from nora.policy import Conversation, Policy
from tests.unit.test_documents_service import (
    OTHER,
    ROOM,
    _doc,
    _policy,
    _publish,
    _service,
)

WILD = ("signal", "open")  # `persons = "*"` in `_policy()`: the room names nobody


async def test_a_wildcard_room_document_is_read_by_its_creator_alone() -> None:
    """Room membership is not a grant in a room that names nobody: `mei` shares the
    room with `ken`'s document, and still reads it exactly like an unknown id."""
    svc, _ = _service()
    made = await _publish(svc, _doc(title="ken only"), room=WILD)
    as_creator = await svc.read(
        platform=WILD[0],
        conversation_id=WILD[1],
        document_id=made.document_id,
        person_id="ken",
    )
    as_member = await svc.read(
        platform=WILD[0],
        conversation_id=WILD[1],
        document_id=made.document_id,
        person_id="mei",
    )
    unknown = await svc.read(
        platform=WILD[0],
        conversation_id=WILD[1],
        document_id=uuid4(),
        person_id="mei",
    )
    assert as_creator.status == "ok" and as_creator.title == "ken only"
    assert as_member.status == "refused" and as_member.spec is None
    assert as_member == unknown


async def test_a_wildcard_room_document_is_not_read_without_a_requester() -> None:
    """The participation and fire turns that name nobody read nothing from a wildcard room
    by room membership, and the model-facing read has no requester to check."""
    svc, _ = _service()
    made = await _publish(svc, _doc(title="ken only"), room=WILD)
    read = await svc.read(platform=WILD[0], conversation_id=WILD[1], document_id=made.document_id)
    assert read.status == "refused" and read.spec is None


async def test_a_document_in_a_listed_room_is_read_by_a_person_the_room_names() -> None:
    """The positive control: `mei` is listed in `room-1`, so she reads `ken`'s document
    there. The wildcard refusal above is the room's shape, not a general one."""
    svc, _ = _service()
    made = await _publish(svc, _doc(title="shared"), room=ROOM)
    read = await svc.read(
        platform=ROOM[0],
        conversation_id=ROOM[1],
        document_id=made.document_id,
        person_id="mei",
    )
    assert read.status == "ok" and read.title == "shared"


async def test_a_person_a_listed_room_no_longer_names_cannot_read_its_document() -> None:
    """Revocation: the same read after `mei` is removed from the room's list is refused."""
    removed = _policy()
    svc, _ = _service(
        rooms_of=lambda p: rooms_named(
            Policy(
                conversations=tuple(
                    c
                    if c.platform != "signal" or "room-1" not in c.ids
                    else Conversation(platform="signal", ids=c.ids, persons=frozenset({"ken"}))
                    for c in removed.conversations
                )
            ),
            p,
        )
    )
    made = await _publish(svc, _doc(title="shared"), room=ROOM)
    read = await svc.read(
        platform=ROOM[0],
        conversation_id=ROOM[1],
        document_id=made.document_id,
        person_id="mei",
    )
    assert read.status == "refused"


async def test_a_wildcard_room_document_cannot_be_revised_by_a_non_creator() -> None:
    """`publish` on a revision applies the same predicate: `mei` gets the same answer as
    for an unknown id, and `ken`'s document keeps its text."""
    svc, store = _service()
    made = await _publish(svc, _doc(title="ken only"), room=WILD)
    result = await svc.publish(
        platform=WILD[0],
        conversation_id=WILD[1],
        person_id="mei",
        raw=_doc(title="taken"),
        written_by="tag:2",
        document_id=made.document_id,
        base_revision=1,
    )
    assert result.status == "refused" and result.findings[0].rule == "no_such_document"
    assert (await store.get(document_id=made.document_id)).summary.title == "ken only"


async def test_a_wildcard_room_document_cannot_have_its_state_changed_by_a_non_creator() -> None:
    svc, store = _service()
    made = await _publish(svc, _doc(title="ken only"), room=WILD)
    result = await svc.set_state(
        platform=WILD[0],
        conversation_id=WILD[1],
        person_id="mei",
        document_id=made.document_id,
        state="archived",
    )
    assert result.status == "refused"
    assert (await store.get(document_id=made.document_id)).summary.state == "active"


async def test_autonomous_document_revision_preserves_creator_content_and_history():

    svc, store = _service(shared_room=lambda u, p, c: u == "u" and (p, c) == ROOM)
    made = await _publish(
        svc, _doc({"type": "Markdown", "props": {"text": "Meet 8pm. Keep this paragraph."}})
    )
    assert made.status == "ok"
    grant = DocumentAuthority("u", frozenset({ROOM}), 42, ((7, *ROOM, "9pm"),))
    result = await svc.publish(
        platform=ROOM[0],
        conversation_id=ROOM[1],
        person_id=grant,
        document_id=made.document_id,
        base_revision=1,
        written_by="must not impersonate",
        raw={
            "changes": [
                {"path": "/root/children/0/props/text", "old": "8pm", "new": "9pm", "sources": [7]}
            ]
        },
    )
    assert result.status == "ok" and result.revision == 2
    saved = await store.get(document_id=made.document_id)
    assert saved.summary.created_by == "ken"
    assert saved.spec["root"]["children"][0]["props"]["text"] == "Meet 9pm. Keep this paragraph."
    history = await store.history(document_id=made.document_id)
    assert [h.revision for h in history] == [1, 2]
    assert history[-1].written_by == "cognition:42;sources:7"
    stale = await svc.publish(
        platform=ROOM[0],
        conversation_id=ROOM[1],
        person_id=grant,
        document_id=made.document_id,
        base_revision=1,
        written_by="",
        raw={"changes": []},
    )
    assert stale.status == "conflict"
    assert len(await store.history(document_id=made.document_id)) == 2


@pytest.mark.parametrize(
    "unit,granted,sharing",
    [
        ("other", frozenset({ROOM}), True),
        ("u", frozenset({OTHER}), True),
        ("u", frozenset({ROOM}), False),
        ("u", frozenset(), True),
    ],
)
async def test_autonomous_documents_deny_unknown_wildcard_foreign_and_nonagenda(
    unit, granted, sharing
):
    from nora.domain.documents import handle_of

    svc, store = _service(shared_room=lambda u, p, c: sharing and u == "u" and (p, c) == ROOM)
    made = await _publish(svc, _doc(title="Generated private title"))
    authority = DocumentAuthority(unit, granted, 42, ((7, *ROOM, "new text"),))
    for kwargs in ({"document_id": made.document_id}, {"handle": handle_of(made.document_id)}):
        result = await svc.read(
            platform=ROOM[0], conversation_id=ROOM[1], person_id=authority, **kwargs
        )
        assert result.status == "refused" and result.document_id is None and not result.title
    result = await svc.publish(
        platform=ROOM[0],
        conversation_id=ROOM[1],
        person_id=authority,
        document_id=made.document_id,
        base_revision=1,
        written_by="",
        raw={"changes": []},
    )
    assert result.status == "refused" and result.document_id is None
    assert len(await store.history(document_id=made.document_id)) == 1


async def test_deferred_document_tools_read_then_revise_and_refuse_stale_read():
    from nora.agent.ports import adapt
    from nora.agent.surface import deferred_surface
    from nora.documents.wire import session_tool_specs

    svc, store = _service(shared_room=lambda u, p, c: u == "u" and (p, c) == ROOM)
    made = await _publish(
        svc, _doc({"type": "Markdown", "props": {"text": "Meet 8pm. Other words."}})
    )
    grant = DocumentAuthority("u", frozenset({ROOM}), 42, ((7, *ROOM, "9pm"),))
    specs = session_tool_specs(svc, None, lambda: grant)
    surface = deferred_surface([adapt(s) for s in specs])
    assert {"document_read", "document_list", "document_revise"} <= surface.shelf.names()
    assert "document_revise" in surface.catalogue
    calls = {s.name: s.call for s in specs}
    denied = await calls["document_revise"](str(made.document_id), 1, [], ":".join(ROOM))
    assert denied["status"] == "refused"
    read = await calls["document_read"](str(made.document_id), ":".join(ROOM))
    assert read["status"] == "ok" and read["revision"] == 1
    changes = [{"path": "/root/children/0/props/text", "old": "8pm", "new": "9pm", "sources": [7]}]
    result = await calls["document_revise"](str(made.document_id), 1, changes, ":".join(ROOM))
    assert result["status"] == "ok" and result["revision"] == 2
    assert (await store.get(document_id=made.document_id)).spec["root"]["children"][0]["props"][
        "text"
    ] == "Meet 9pm. Other words."


@pytest.mark.parametrize(
    "scenario",
    ["uncertain", "foreign_evidence", "no_evidence", "historical", "archived", "bool_source"],
)
async def test_autonomous_document_revisions_fail_closed_on_unsupported_evidence(scenario):

    svc, store = _service(shared_room=lambda u, p, c: u == "u" and (p, c) == ROOM)
    made = await _publish(
        svc, _doc({"type": "Markdown", "props": {"text": "Meet 8pm. Keep this."}})
    )
    evidence = ((7, *ROOM, "Maybe 9pm"),)
    if scenario == "foreign_evidence":
        evidence = ((7, *OTHER, "9pm"),)
    if scenario == "no_evidence":
        evidence = ()
    if scenario == "bool_source":
        evidence = ((1, *ROOM, "9pm"),)
    grant = DocumentAuthority(
        "u", frozenset({ROOM}), 42, evidence, read_only=scenario == "historical"
    )
    if scenario == "archived":
        await store.set_state(document_id=made.document_id, state="archived", set_by="ken")
    result = await svc.publish(
        platform=ROOM[0],
        conversation_id=ROOM[1],
        person_id=grant,
        document_id=made.document_id,
        base_revision=1,
        written_by="",
        raw={
            "changes": [
                {
                    "path": "/root/children/0/props/text",
                    "old": "8pm",
                    "new": "9pm",
                    "sources": [True] if scenario == "bool_source" else [7],
                }
            ]
        },
    )
    assert result.status == "refused"
    saved = await store.get(document_id=made.document_id)
    assert saved.summary.revision == 1 and "8pm" in saved.text
    assert saved.summary.state == ("archived" if scenario == "archived" else "active")

