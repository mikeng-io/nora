"""The shared note index's opt-in to carry a note's REAL id as `n`.

The Cognition session's `note_revise` takes the note's own id, so the id it is
shown must BE that id (`ids=True`); the tag turn keeps its positional `1..N`
(`resolve_positions` remaps it), which `test_tag_notes_block_is_unchanged_by_
step3b.py` pins byte for byte. These pin the opt-in itself: what it changes,
what it leaves, and that it cannot widen the row.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from nora.domain.cognition_ground import NoteLine
from nora.domain.notes import Note
from nora.language.note_render import render_note_rows


def _note(n: int, title: str = "Dinner", description: str = "which venue", **kw: object) -> Note:
    return Note(
        id=f"note-{n}", unit="u", title=title, description=description,
        uncertainty="u", closing_condition="c", established_in="g1", created_at=1,
        head_revision_id=f"rev-{n}", anchor_entity_ids=("p1",), **kw,  # type: ignore[arg-type]
    )


def _rows(text: str) -> list[ET.Element]:
    return list(ET.fromstring(f"<r>{text}</r>"))  # noqa: S314 - our own bytes, rooted before parse


def test_the_default_is_the_positional_n_the_tag_turn_remaps():
    rows = _rows(render_note_rows([_note(7), _note(9)]))
    assert [r.get("n") for r in rows] == ["1", "2"]


def test_the_opt_in_carries_the_notes_own_id():
    rows = _rows(render_note_rows([_note(7), _note(9)], ids=True))
    assert [r.get("n") for r in rows] == ["note-7", "note-9"]
    assert [r.get("title") for r in rows] == ["Dinner", "Dinner"]
    assert [r.text for r in rows] == ["which venue", "which venue"]


def test_a_note_line_with_no_anchor_renders_through_the_same_function():
    """The session's index lines carry id, title and description and nothing
    else; they render, with no `about` (a line has no anchor day to read)."""
    out = render_note_rows(
        [NoteLine(id="n-77", title="Trip", description="dates")], today=20260930, ids=True,
    )
    assert out == '<note n="n-77" title="Trip">dates</note>'


def test_a_note_with_an_anchor_keeps_its_about_beside_the_real_id():
    out = render_note_rows([_note(1, anchor_date=20260930)], today=20260930, ids=True)
    assert out.startswith('<note n="note-1" title="Dinner" about="Wed 30 Sep, today">')


def test_the_opt_in_changes_n_and_nothing_else():
    notes = [_note(1, anchor_date=20260901), _note(2)]
    plain = render_note_rows(notes, today=20260930)
    real = render_note_rows(notes, today=20260930, ids=True)
    assert plain.replace('n="1"', 'n="note-1"').replace('n="2"', 'n="note-2"') == real


def test_an_id_that_tries_to_close_its_attribute_cannot_write_one():
    """The id is a stored value like any other and is escaped, not trusted."""
    out = render_note_rows(
        [NoteLine(id='x" title="forged', title="Real", description="d")], ids=True,
    )
    row = _rows(out)[0]
    assert row.get("n") == 'x" title="forged' and row.get("title") == "Real"
