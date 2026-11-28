"""A record read names rows through the room they came from, and keeps a deleted row's words out."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from nora.agent.toolkit.rooms import _name_rows
from nora.conversation.adapters.tools import query as module
from nora.domain.managed import room_scope_id
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.record_query import RecordHit

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _row(body="what was said"):
    return TranscriptRow(
        id=7, sender_id="+852", sender_name="A", ts=1_000, body=body, is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


class _Access:
    def __init__(self):
        self.scopes = []

    async def name_transcript(self, rows, *, platform, scope):
        self.scopes.append(scope)
        return rows


def test_rows_are_named_through_the_room_they_were_read_in_not_the_callers_room():
    access = _Access()
    callers_room = SimpleNamespace(access=access, platform="signal", conversation_id="agenda-room")
    asyncio.run(_name_rows(
        None, callers_room, [_row()], platform="signal", conversation_id="read-room",
    ))
    assert access.scopes[0].id == room_scope_id("signal", "read-room")


def test_a_deleted_hit_is_reported_shown_without_its_words():
    shown = []
    make = module._make__record_messages(
        SimpleNamespace(display_name="Nora", on_rows=shown.extend),
    )
    deleted = RecordHit(platform="signal", conversation_id="g1", row=_row(), deleted=True)
    live = RecordHit(platform="signal", conversation_id="g1", row=_row("kept"), deleted=False)
    out = make([deleted, live], [deleted.row, live.row], rooms=[], near=frozenset(), now=NOW)
    assert out[0]["deleted"] is True and out[0]["body"] is None
    assert [r.body for r in shown] == [None, "kept"]
