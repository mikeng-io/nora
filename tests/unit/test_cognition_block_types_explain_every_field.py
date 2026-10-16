"""A field cannot ship unexplained, and the type text is said once.

`BLOCK_TYPES` is the channel the Cognition model learns its input from: what a
message's `n` is for, whose name `from` is, what a cut means. Two things go wrong
silently: a renderer grows an attribute nobody glossed, so it reaches the model
undefined, and a block repeats its type text per item. Both are measured here
from the RENDERED prompt, not from the source.
"""

from __future__ import annotations

from collections import defaultdict
from zoneinfo import ZoneInfo

import pytest

from nora.domain.block_types import BLOCK_TYPES
from nora.domain.cognition_ground import (
    EventLine,
    Grounding,
    NoteLine,
    RecordItem,
    RollupLine,
)
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.prompt import escape_attr
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import RenderBudget, render_agenda
from tests.unit.cognition_view import blocks

HK = ZoneInfo("Asia/Hong_Kong")
NOW = 1_800_000_000_000
A, B = Room("signal", "room-a"), Room("discord", "room-b")
LABELS = {"signal:room-a": "Room A", "discord:room-b": "Room B"}

#: The block type each element's attributes are glossed in.
TYPE_OF = {
    "item": "agenda", "m": "agenda", "rollup": "agenda",
    "note": "note_index", "note_index": "note_index",
    "event": "events", "room_events": "events",
}


def _stim(sid: int, room: Room | None, *, kind: str = "message", ref: str | None = None,
          ago: int = 10, cues: tuple[str, ...] = ()) -> Stimulus:
    return Stimulus(
        id=sid, unit="u", kind=kind, item=kind, ref=ref or f"messages:{sid}",
        at_ms=NOW - ago * 60_000, recorded_at_ms=NOW,
        platform=room.platform if room else None,
        conversation_id=room.conversation_id if room else None, cues=cues,
    )


def _rec(sid: int, kind: str, *, mid: int, text: str | None, room: Room = A, emoji: str = "",
         edited: bool = False, own: bool = False, author: str = "Ada") -> RecordItem:
    return RecordItem(
        ref=f"messages:{sid}", kind=kind, message_id=mid, room=room, author=author,  # type: ignore[arg-type]
        at_ms=NOW - 10 * 60_000, text=text, emoji=emoji, edited=edited, own=own,
    )


def _everything() -> str:
    """A prompt that makes every element carry every attribute it can: a message
    with cues and an edit, a reaction, a deletion, a withdrawn body, her own send,
    a Rollup row, a returning item, an item with older messages cut and one with an
    unreadable stimulus, a note index and an event list each with something left
    out, an event with a venue and a revision and one with neither."""
    stims = {
        1: _stim(1, A, cues=("datetime",)), 2: _stim(2, A, ref="message_reaction:2"),
        3: _stim(3, A, ref="message_delete:3"), 4: _stim(4, A), 5: _stim(5, A),
        6: _stim(6, B), 7: _stim(7, B), 8: _stim(8, B),
        9: _stim(9, None, kind="rollup", ref="arrival|x|1"),
    }
    roll = RollupLine(ref="arrival|x|1", kind="arrival", names=("Ada",), person_ids=("ada",),
                      start_ms=NOW - 600_000, end_ms=None, place="Ramen Ya")
    records = {
        "messages:1": _rec(1, "edit", mid=101, text="friday at 9", edited=True),
        "message_reaction:2": _rec(2, "reaction", mid=44, text="see you", emoji="👍"),
        "message_delete:3": _rec(3, "delete", mid=45, text=None),
        "messages:4": _rec(4, "message", mid=46, text=None),          # withdrawn words
        "messages:6": _rec(6, "message", mid=601, text="x" * 300, room=B),
        "messages:7": _rec(7, "message", mid=602, text="y" * 300, room=B),
    }                                                                  # 5 and 8: unreadable
    grounding = Grounding(
        records=records, rollups={roll.ref: roll}, room_labels=LABELS,
        own_sends={0: (_rec(50, "message", mid=50, text="noted", own=True, author="Nora"),)},
        notes=(
            NoteLine(id="n-0", title="Note 0", description="d", review="anchor_passed",
                     may_duplicate="n-1"),
            *(NoteLine(id=f"n-{i}", title=f"Note {i}", description="d") for i in (1, 2)),
        ),
        events=(
            EventLine(id="e-1", room=A, title="Dinner", when_ms=NOW, venue="Ramen Ya",
                      status="proposed", revision="rev-1"),
            EventLine(id="e-2", room=A, title="Walk", when_ms=None, venue="", status="staged"),
            EventLine(id="e-3", room=B, title="Games", when_ms=NOW, venue="", status="proposed"),
        ),
    )
    agenda = (
        AgendaItem(kind="message", room=A, summary="5 new messages", cues=(), surprise=1.0,
                   stimulus_ids=(1, 2, 3, 4, 5), strike=2),
        AgendaItem(kind="message", room=B, summary="3 new messages", cues=(), surprise=1.0,
                   stimulus_ids=(6, 7, 8)),
        AgendaItem(kind="rollup", room=A, summary="a rollup row changed", cues=(),
                   surprise=1.0, stimulus_ids=(9,)),
    )
    return render_agenda(
        agenda, stimuli=stims, grounding=grounding, now_ms=NOW, tz=HK,
        budget=RenderBudget(record_chars=900, min_item_chars=100, register_lines=1),
    )


def _emitted() -> dict[str, set[str]]:
    """Every attribute name each element of the rendered prompt carries, by the
    block type that must gloss it."""
    seen: dict[str, set[str]] = defaultdict(set)
    for block in blocks(_everything()):
        if block.tag in ("note_index", "events"):
            seen[block.tag].update(k for k in block.attrib if k not in ("description", "fields"))
        for el in block.iter():
            if el is block or el.tag not in TYPE_OF:
                continue
            seen[TYPE_OF[el.tag]].update(el.attrib)
    return seen


def test_every_attribute_the_prompt_emits_has_a_gloss_in_its_block_type():
    seen = _emitted()
    assert set(seen) == {"agenda", "note_index", "events"}
    for type_name, attrs in seen.items():
        glossed = dict(BLOCK_TYPES[type_name].annotations)
        for attr in sorted(attrs):
            assert attr in glossed, f"<{type_name}> emits `{attr}`, glossed nowhere"
            assert glossed[attr].strip() and len(glossed[attr].split()) >= 2, (type_name, attr)


def test_the_fixture_makes_every_element_carry_every_attribute_it_can():
    """An instrument that cannot see is no evidence: the prompt above must really
    exercise every field, or the test before it passes by not looking."""
    seen = _emitted()
    assert seen["agenda"] == {
        "index", "kind", "room", "summary", "returning", "window", "earlier_not_shown",
        "earlier_range", "unreadable", "n", "at", "from", "edited", "reaction",
        "withdrawn", "deleted", "own", "cues",
    }
    assert seen["note_index"] == {"n", "title", "not_listed", "review", "may_duplicate"}
    assert seen["events"] == {
        "room", "not_listed", "id", "status", "when", "venue", "revision",
    }


def test_no_annotation_glosses_a_field_that_is_never_emitted():
    seen = _emitted()
    for type_name in ("agenda", "note_index", "events"):
        assert set(dict(BLOCK_TYPES[type_name].annotations)) == seen[type_name], type_name


# ---- the type text is said once -------------------------------------------------------


def _agenda_of(n_items: int) -> str:
    stims: dict[int, Stimulus] = {}
    records: dict[str, RecordItem] = {}
    agenda = []
    for i in range(n_items):
        room = Room("signal", f"room-{i}")
        stims[i + 1] = _stim(i + 1, room)
        records[f"messages:{i + 1}"] = _rec(i + 1, "message", mid=100 + i, text="hello there",
                                            room=room)
        agenda.append(AgendaItem(kind="message", room=room, summary="1 new message in this room",
                                 cues=(), surprise=1.0, stimulus_ids=(i + 1,)))
    events = tuple(
        EventLine(id=f"e-{i}", room=a.room, title="Dinner", when_ms=NOW, venue="",
                  status="proposed") for i, a in enumerate(agenda)
    )
    return render_agenda(
        agenda, stimuli=stims, grounding=Grounding(records=records, events=events),
        now_ms=NOW, tz=HK,
    )


@pytest.mark.parametrize("n_items", [1, 5, 12])
def test_the_type_text_appears_once_per_prompt_however_many_items(n_items: int):
    prompt = _agenda_of(n_items)
    for name in ("agenda", "events"):
        assert prompt.count(escape_attr(BLOCK_TYPES[name].description)) == 1, name
        assert prompt.count(f"<{name} ") == 1 or prompt.count(f"<{name}>") == 1, name
    assert prompt.count("<item ") == n_items
    assert prompt.count("<room_events ") == n_items
    assert prompt.count('description="') == 3        # clock-less: agenda, events and nothing else


def test_twelve_items_cost_their_own_rows_and_not_twelve_copies_of_the_type_text():
    """An item adds its attributes and its rows, a few hundred characters, never
    a repeat of the block's type text."""
    one, five, twelve = (len(_agenda_of(n)) for n in (1, 5, 12))
    per_item = (twelve - one) / 11
    assert per_item < 600, per_item                       # item + one row + one event
    assert (five - one) / 4 < 600
    assert twelve < 12_000, twelve
    type_text = sum(
        len(BLOCK_TYPES[n].description)
        + sum(len(k) + len(v) + 6 for k, v in BLOCK_TYPES[n].annotations)
        for n in ("agenda", "events")
    )
    assert type_text < 3_200, type_text          # said once: the 12-item prompt holds it once


# ---- the test helper itself sees what it claims to ------------------------------------


def test_the_view_rejects_text_outside_a_block_and_between_an_elements_children():
    """`cognition_view.blocks` is what every prompt test reads through; one that
    ignored text between elements would let a stray line of prose pass as a prompt."""
    with pytest.raises(AssertionError):
        blocks("a line of prose<clock>x</clock>")
    with pytest.raises(AssertionError):
        blocks("<clock>x</clock>and after")
    with pytest.raises(AssertionError):
        blocks("<agenda>stray<item index='0'/></agenda>")
    with pytest.raises(AssertionError):
        blocks("<agenda><item index='0'/>stray</agenda>")
    clean = blocks("<clock>x</clock>\n\n<agenda>\n<item index='0'/>\n</agenda>")
    assert [b.tag for b in clean] == ["clock", "agenda"]


def test_every_prompt_the_fixture_renders_has_no_stray_text():
    assert [b.tag for b in blocks(_everything())] == ["clock", "agenda", "note_index", "events"]


# ---- the line shapes the description teaches are the ones the validator holds ---------


def test_the_owed_and_ruled_out_shapes_the_description_teaches_are_the_validators():
    """`agenda`'s description quotes two line shapes. They are read off
    `parse_note_body`, not off a comment: an Owed line takes a roster name, never Nora, a due and NO
    `(names)`; a Ruled out line takes `(names)` (it is in `ATTRIBUTED`)."""
    from nora.domain.note_body import NoteBodyError, Shown, ShownMessage, parse_note_body

    shown = Shown(
        rendered=(ShownMessage(id=7, sender="Ada", person_id="p1"),),
        roster={"Ada": "p1"}, nora_names=frozenset({"Nora"}),
    )

    def parse(*lines: str) -> None:
        parse_note_body("## Decided\n- go (Ada) [7]\n## Open\n- where [7]\n" + "\n".join(lines),
                        shown=shown)

    for due in ("2026-10-03", "2026-10-03 Sat 19:30", "when the booking opens", "no date given"):
        parse(f"## Owed\n- Ada -> book -> {due} [7]")                       # the shape taught
    with pytest.raises(NoteBodyError, match="unknown_owner"):
        parse("## Owed\n- Nora -> book -> no date given [7]")              # never you
    with pytest.raises(NoteBodyError, match="unknown_owner"):
        parse("## Owed\n- Zed -> book -> no date given [7]")                # on the roster
    with pytest.raises(NoteBodyError, match="bad_date"):
        parse("## Owed\n- Ada -> book -> tomorrow [7]")
    parse("## Ruled out\n- the pier: too far (Ada) [7]")                    # the shape taught
    # It takes (names); left out, code completes them from the cited senders.
    completed = parse_note_body(
        "## Decided\n- go (Ada) [7]\n## Open\n- where [7]\n## Ruled out\n- the pier: too far [7]",
        shown=shown,
    )
    assert completed.text.endswith("- the pier: too far (Ada) [7]")
    text = BLOCK_TYPES["agenda"].description
    assert "-> <due> [<ids>]" in text and "<option>: <reason> (<names>) [<ids>]" in text
