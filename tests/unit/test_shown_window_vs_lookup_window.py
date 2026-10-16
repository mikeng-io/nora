"""`#n` numbers the rows the prompt SHOWED; `who_said` searches the deeper read.

`[store] prompt_window` shows fewer rows than are read. `send_reply(reply_to=n)`
and a note's `[#n]` must resolve against the shown rows (what the model saw
numbered), while `who_said` keeps the deeper `lookup_window`. Sharing one
tuple would make `reply_to=2` quote an older, unseen row.
"""

from __future__ import annotations

from nora.agent.registry import ToolRegistry
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore
from nora.testing.note_store import InMemoryNoteStore
from tests.fakes import SinkSender
from tests.test_note_tools import (
    GROUP,
    MILES,
    NOW,
    UNIT,
    _FakePersons,
    _NullStore,
    _room,
)


def _row(i: int) -> TranscriptRow:
    return TranscriptRow(
        id=1000 + i, sender_id="+85200000001", sender_name="miles", ts=NOW - (10 - i) * 1000,
        body=f"line {i}" if i != 3 else "收隊 now", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_name="Miles Novak", person_id="miles",
    )


READ = tuple(_row(i) for i in range(10))     # what was read: ids 1000..1009
SHOWN = READ[-3:]                            # what the prompt numbered #1..#3: 1007..1009


class _Deliverer(SinkSender):
    held_count = 0

    def __init__(self) -> None:
        super().__init__()
        self.quotes: list = []

    async def deliver(self, text, **kw):
        self.quotes.append(kw.get("quote"))
        return True


def _tools(deliverer=None, notes=None) -> dict:
    registry = ToolRegistry(notes=notes, note_unit=lambda _p, _c: UNIT)
    specs = registry.specs(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=_FakePersons(MILES), honcho=FakeMemoryStore(),
        deliverer=deliverer or _Deliverer(), metrics=Metrics(), room=_room(),
        transcript=SHOWN, lookup_window=READ,
    )
    return {spec.name: spec.call for spec in specs}


async def test_reply_to_n_quotes_the_nth_shown_row_not_the_nth_read_row():
    deliverer = _Deliverer()
    await _tools(deliverer)["send_reply"](text="ok", reply_to=2)
    assert deliverer.quotes[0].ts == SHOWN[1].ts          # #2 of the 3 shown
    assert deliverer.quotes[0].ts != READ[1].ts           # not the 2nd read row


async def test_reply_to_beyond_the_shown_rows_is_sent_unthreaded():
    deliverer = _Deliverer()
    await _tools(deliverer)["send_reply"](text="ok", reply_to=len(SHOWN) + 1)
    assert deliverer.quotes == [None]


async def test_a_note_citation_maps_to_the_nth_shown_rows_id():
    store = InMemoryNoteStore()
    out = await _tools(notes=store)["note_create"](
        title="Dinner", description="d", uncertainty="u", closing_condition="c",
        body="## Decided\n## Open\n- where [#2]", anchor_date=20261011,
    )
    assert out["status"] == "ok", out
    assert len(await store.standing(unit=UNIT)) == 1
    bodies = [r.body for r in store.revisions.values()]
    assert any(f"[{SHOWN[1].id}]" in b for b in bodies), bodies
    assert not any(f"[{READ[1].id}]" in b for b in bodies)


async def test_who_said_still_searches_the_whole_read():
    out = await _tools()["who_said"](words="收隊")        # said in READ[3], not in SHOWN
    assert out["status"] == "ok", out
