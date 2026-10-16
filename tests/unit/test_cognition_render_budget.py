"""The Cognition prompt's size budget: `_fit`, `_shares` and
`RenderBudget` are a row-SELECTION pre-step, the blocks render what they chose,
and a row is never cut through its own element.

Held by `test_cognition_grounding.py` and by the baseline parity test: the
floors, the shares by need, the hard cap on the Record rows together, the NEWEST
rows kept, the cut said with a count and a range. What these add is what a row
being an element, not a line, could break: a row clipped to its share is still a
whole element, the cap holds across every budget a real item can be handed, and the Register's
rows are left out whole and counted.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from nora.domain.cognition_ground import EventLine, Grounding, NoteLine, RecordItem
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import RenderBudget, render_agenda
from tests.unit.cognition_view import blocks, events, ids, items, named, rows

HK = ZoneInfo("Asia/Hong_Kong")
NOW = 1_800_000_000_000
ROOMS = [Room("signal", f"room-{i}") for i in range(3)]
LABELS = {f"signal:room-{i}": f"Room {i}" for i in range(3)}


def _world(
    per_room: int = 12, words: int = 150,
) -> tuple[list[AgendaItem], dict[int, Stimulus], Grounding]:
    stimuli: dict[int, Stimulus] = {}
    records: dict[str, RecordItem] = {}
    agenda: list[AgendaItem] = []
    for r, room in enumerate(ROOMS):
        sids = []
        for m in range(per_room):
            sid = 100 * r + m + 1
            ref = f"messages:{sid}"
            stimuli[sid] = Stimulus(
                id=sid, unit="u", kind="message", item="message", ref=ref,
                at_ms=NOW - (60 - m) * 60_000, recorded_at_ms=NOW,
                platform=room.platform, conversation_id=room.conversation_id,
            )
            records[ref] = RecordItem(
                ref=ref, kind="message", message_id=1000 * r + m + 1, room=room,
                author=f"P{m % 3}", at_ms=stimuli[sid].at_ms, text=f"r{r} m{m} " + "w" * words,
            )
            sids.append(sid)
        agenda.append(AgendaItem(
            kind="message", room=room, summary=f"{per_room} new messages in this room",
            stimulus_ids=tuple(sids), cues=(), surprise=1.0,
        ))
    return agenda, stimuli, Grounding(records=records, room_labels=LABELS)


def _prompt(budget: RenderBudget, **world: int) -> str:
    agenda, stimuli, grounding = _world(**world)
    return render_agenda(
        agenda, stimuli=stimuli, grounding=grounding, now_ms=NOW, tz=HK, budget=budget,
    )


def _spent(prompt: str) -> int:
    return sum(len(ln) + 1 for ln in prompt.splitlines() if ln.startswith("<m "))


@pytest.mark.parametrize("record_chars", range(400, 3401, 200))
def test_the_record_rows_never_spend_more_than_the_budget_and_each_item_keeps_its_newest(
    record_chars: int,
):
    prompt = _prompt(RenderBudget(record_chars=record_chars, min_item_chars=900))
    assert _spent(prompt) <= record_chars
    blocks(prompt)                                   # still well-formed whatever was cut
    for r, item in enumerate(items(prompt)):
        shown = ids(item)
        assert shown and shown[-1] == 1000 * r + 12          # the NEWEST is always there
        assert shown == list(range(shown[0], shown[-1] + 1))  # a contiguous run: oldest dropped
        left_out = 12 - len(shown)
        assert item.attrib.get("earlier_not_shown") == (str(left_out) if left_out else None)


@pytest.mark.parametrize("record_chars", [0, 1, 20, 60])
def test_an_allowance_too_small_for_one_row_keeps_one_whole_element_with_a_character_of_words(
    record_chars: int,
):
    """The one place the cap yields, and the reason: the element is what keeps one
    row one row. A row cut through its own tag would be the forgery this
    format closes."""
    prompt = _prompt(RenderBudget(record_chars=record_chars, min_item_chars=0))
    agenda = items(prompt)                            # parses: no element was cut
    assert len(agenda) == 3
    for item in agenda:
        (row,) = rows(item)
        assert row.tag == "m" and row.text is not None and row.text.endswith("…")
        assert len(row.text) == 2                     # one character of words, then the cut
        assert item.attrib["earlier_not_shown"] == "11"


def test_a_row_clipped_to_its_share_is_a_whole_element_with_its_words_cut():
    prompt = _prompt(
        RenderBudget(record_chars=300, min_item_chars=100, message_chars=400), per_room=1,
        words=5000,
    )
    for item in items(prompt):
        (row,) = rows(item)
        assert row.attrib["n"] and row.attrib["from"] and row.attrib["at"]
        assert (row.text or "").endswith("…") and 0 < len(row.text or "") < 200
    assert _spent(prompt) <= 300


def test_a_messages_words_are_cut_at_message_chars():
    prompt = _prompt(RenderBudget(message_chars=20), per_room=1)
    for item in items(prompt):
        (row,) = rows(item)
        assert len(row.text or "") == 20 and (row.text or "").endswith("…")


def test_the_cut_range_names_the_oldest_ids_left_out():
    prompt = _prompt(RenderBudget(record_chars=1200, min_item_chars=400))
    for r, item in enumerate(items(prompt)):
        left_out = int(item.attrib["earlier_not_shown"])
        assert item.attrib["earlier_range"] == f"{1000 * r + 1} to {1000 * r + left_out}"


# ---- the Register ---------------------------------------------------------------------


def _register(notes: int, events: int, budget: RenderBudget) -> str:
    agenda, stimuli, grounding = _world(per_room=1, words=5)
    grounding = Grounding(
        records=grounding.records, room_labels=LABELS,
        notes=tuple(
            NoteLine(id=f"n-{i}", title=f"Note {i}", description="d" * 150) for i in range(notes)
        ),
        events=tuple(
            EventLine(id=f"e-{r}-{i}", room=room, title=f"Event {i}", when_ms=NOW + i * 60_000,
                      venue="", status="proposed")
            for r, room in enumerate(ROOMS) for i in range(events)
        ),
    )
    return render_agenda(
        agenda, stimuli=stimuli, grounding=grounding, now_ms=NOW, tz=HK, budget=budget,
    )


def test_a_list_past_register_lines_shows_that_many_rows_and_counts_the_rest():
    prompt = _register(7, 5, RenderBudget(register_lines=3))
    (note_index,) = named(prompt, "note_index")
    assert len(list(note_index)) == 3 and note_index.attrib["not_listed"] == "4"
    groups = events(prompt)
    assert [len(list(g)) for g in groups] == [3, 3, 3]
    assert [g.attrib["not_listed"] for g in groups] == ["2", "2", "2"]
    assert [g.attrib["room"] for g in groups] == ["«Room 0»", "«Room 1»", "«Room 2»"]


def test_a_list_within_every_cap_says_nothing_was_left_out():
    prompt = _register(2, 2, RenderBudget())
    assert "not_listed" not in prompt.replace("`not_listed` is", "")


def test_the_character_budget_leaves_out_whole_rows_and_the_blocks_after_it_say_so():
    prompt = _register(30, 3, RenderBudget(register_lines=40, register_chars=1000))
    top = blocks(prompt)                                  # no row was cut through its element
    (note_index,) = named(prompt, "note_index")
    shown = len(list(note_index))
    assert 0 < shown < 30 and note_index.attrib["not_listed"] == str(30 - shown)
    assert sum(len(ln) + 1 for ln in prompt.splitlines() if ln.startswith("<note ")) <= 1000
    # nothing after the cut is listed, and each block after it says how much it holds back
    assert [b.tag for b in top] == ["clock", "agenda", "note_index", "events"]
    groups = events(prompt)
    assert [len(list(g)) for g in groups] == [0, 0, 0]
    assert [g.attrib["not_listed"] for g in groups] == ["3", "3", "3"]


def test_every_note_and_event_is_listed_with_its_own_id_and_an_old_description_is_one_line():
    agenda, stimuli, grounding = _world(per_room=1, words=5)
    grounding = Grounding(
        records=grounding.records, room_labels=LABELS,
        notes=(NoteLine(id="n-1", title="T", description="line one\nline two " + "x" * 300),),
    )
    prompt = render_agenda(agenda, stimuli=stimuli, grounding=grounding, now_ms=NOW, tz=HK)
    (note,) = list(named(prompt, "note_index")[0])
    assert note.attrib["n"] == "n-1"
    assert note.text is not None and "\n" not in note.text
    assert note.text.startswith("line one line two ") and note.text.endswith("…")
    assert len(note.text) == 200


# ---- the cap is measured on what is RENDERED ------------------------------------------


def _markup_prompt(record_chars: int, *, items_: int = 12, text: str = '&<"' * 140) -> str:
    """`items_` items of one message each, every message 420 characters that
    escape to about 1,900 (`&` is five characters, `<` four, `"` six in an
    attribute and one in text): the shape on which a cut by RAW length would
    overspend its share many times over."""
    stimuli: dict[int, Stimulus] = {}
    records: dict[str, RecordItem] = {}
    agenda: list[AgendaItem] = []
    for i in range(items_):
        room = Room("signal", f"room-{i}")
        ref = f"messages:{i + 1}"
        stimuli[i + 1] = Stimulus(
            id=i + 1, unit="u", kind="message", item="message", ref=ref, at_ms=NOW - 60_000,
            recorded_at_ms=NOW, platform=room.platform, conversation_id=room.conversation_id,
        )
        records[ref] = RecordItem(
            ref=ref, kind="message", message_id=500 + i, room=room, author="Tom & Jerry",
            at_ms=NOW - 60_000, text=text,
        )
        agenda.append(AgendaItem(
            kind="message", room=room, summary="1 new message in this room",
            stimulus_ids=(i + 1,), cues=(), surprise=1.0,
        ))
    return render_agenda(
        agenda, stimuli=stimuli, grounding=Grounding(records=records), now_ms=NOW, tz=HK,
        budget=RenderBudget(record_chars=record_chars),
    )


@pytest.mark.parametrize("record_chars", [*range(2000, 12001, 500), 8000])
def test_markup_heavy_words_cannot_spend_more_than_the_budget_once_escaped(record_chars: int):
    prompt = _markup_prompt(record_chars)
    assert _spent(prompt) <= record_chars, (record_chars, _spent(prompt))
    agenda = items(prompt)                              # whole and well-formed
    assert len(agenda) == 12
    for item in agenda:
        (row,) = rows(item)                             # each keeps its one row,
        assert row.text and row.text.endswith("…")      # its words cut to fit
        assert row.text.startswith('&<"&<"')            # the START of what was said


def test_an_escaped_cut_keeps_as_much_of_the_words_as_fits():
    """Not merely under the cap: the cut is the LARGEST that fits, so a markup-heavy
    row is not thrown away for being markup."""
    (item,) = items(_markup_prompt(900, items_=1))
    (line,) = (ln for ln in _markup_prompt(900, items_=1).splitlines() if ln.startswith("<m "))
    assert len(line) + 1 <= 900 and len(line) + 1 > 900 - 12
    (row,) = rows(item)
    assert len(row.text or "") > 100


# ---- the memos (DEC-306): unit-wide, titles and ids, on their own budget ----------------


def _memo(i: int) -> object:
    from nora.domain.memo import Memo, MemoRevision

    head = MemoRevision(
        id=f"r-{i}", memo_id=f"m-{i}", seq=1, title=f"Memo {i} " + "t" * 60,
        body=f"## Kept\n- the code is secret-{i} [1]", citations=("1",), mode="told",
        state="kept", written_at=NOW - 1000 * i, established_by="cognition:u",
    )
    return Memo(id=f"m-{i}", unit="u", platform="signal", conversation_id="room-0",
                created_at=NOW, head=head)


def test_the_memos_block_comes_last_newest_first_and_never_shows_what_a_memo_keeps():
    agenda, stimuli, grounding = _world(per_room=1, words=5)
    grounding = Grounding(
        records=grounding.records, room_labels=LABELS,
        notes=(NoteLine(id="n-1", title="T", description="d"),),
        memos=tuple(_memo(i) for i in (3, 0, 4, 1, 2)),  # type: ignore[arg-type]
    )
    prompt = render_agenda(
        agenda, stimuli=stimuli, grounding=grounding, now_ms=NOW, tz=HK,
        budget=RenderBudget(memo_lines=3),
    )
    assert [b.tag for b in blocks(prompt)] == ["clock", "agenda", "note_index", "memos"]
    (memos,) = named(prompt, "memos")
    assert [m.attrib["id"] for m in memos] == ["m-0", "m-1", "m-2"]
    assert memos.attrib["not_listed"] == "2"
    assert "secret-" not in prompt


def test_many_memos_never_push_an_agenda_rooms_events_out():
    """Memos do not draw on `register_chars`: on their own budget, after the events,
    they cannot leave every event unlisted."""
    agenda, stimuli, grounding = _world(per_room=1, words=5)
    grounding = Grounding(
        records=grounding.records, room_labels=LABELS,
        events=tuple(
            EventLine(id=f"e-{r}", room=room, title=f"Event {r}", when_ms=NOW,
                      venue="", status="proposed")
            for r, room in enumerate(ROOMS)
        ),
        memos=tuple(_memo(i) for i in range(200)),  # type: ignore[arg-type]
    )
    def shown(register_chars: int) -> tuple[str, list[int], int]:
        prompt = render_agenda(
            agenda, stimuli=stimuli, grounding=grounding, now_ms=NOW, tz=HK,
            budget=RenderBudget(register_chars=register_chars),
        )
        (memos,) = named(prompt, "memos")
        return prompt, [len(list(g)) for g in events(prompt)], len(list(memos))

    prompt, listed_events, listed_memos = shown(600)
    assert listed_events == [1, 1, 1]
    assert [b.tag for b in blocks(prompt)][-2:] == ["events", "memos"]
    (memos,) = named(prompt, "memos")
    assert 0 < listed_memos <= 20 and int(memos.attrib["not_listed"]) == 200 - listed_memos
    assert sum(len(ln) + 1 for ln in prompt.splitlines() if ln.startswith("<memo ")) <= 1200
    # Neither draws on the other: the memos list as many under a tight
    # Register budget as under a generous one.
    assert shown(100_000)[2] == listed_memos


def test_no_memos_block_when_nothing_is_kept():
    agenda, stimuli, grounding = _world(per_room=1, words=5)
    prompt = render_agenda(agenda, stimuli=stimuli, grounding=grounding, now_ms=NOW, tz=HK)
    assert "<memos" not in prompt
