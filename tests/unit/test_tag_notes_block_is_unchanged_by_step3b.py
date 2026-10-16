"""The shared note renderer (`nora.language.note_render.render_note_rows`) has an
opt-in to carry a note's REAL id as `n`, for the Cognition session. The tag path
keeps the positional `n` (`1..N`, remapped back by `resolve_positions`), and not
one byte of its `<notes>` block may change.

**The fixture is the criterion**, captured before the opt-in existed:
`build_prefill`'s whole `<notes ...>...</notes>` block over several note sets
(one, several, an anchor in each distance wording, a retired note that must not
render, titles and descriptions carrying markup, a newline, a dash, and a
quote). What is compared is the block's bytes, not a substring. The existing
renderer tests (`test_note_render.py`, `test_prefill.py`) pass beside it.
Nothing here regenerates the fixture.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from nora.agent.prefill import build_prefill
from nora.domain.models import NormalizedMessage, TagContext
from nora.domain.notes import Note

GOLDEN: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "tag_notes_block_pre3b.json")
    .read_text(encoding="utf-8"),
)

_BLOCK = re.compile(r"<notes .*?</notes>", re.DOTALL)


def _note(n: int, title: str, description: str, **kw: object) -> Note:
    return Note(
        id=f"0191f2c4-0000-7000-8000-{n:012d}", unit="nora", title=title,
        description=description, uncertainty="u", closing_condition="c",
        established_in="g1", created_at=1, head_revision_id=f"rev-{n}",
        anchor_entity_ids=("p1",), **kw,  # type: ignore[arg-type]
    )


def _block(notes: tuple[Note, ...]) -> str:
    """The `<notes>` block of one tag turn's prefill, or `""` when there is none."""
    msg = NormalizedMessage("signal", "g1", "group", "+852111", "Miles", 1, "hi", {})
    ctx = TagContext("tag-1", msg, None, (), (), 0.0, notes=notes)
    text = "\n".join(m["content"][0]["text"] for m in build_prefill(
        ctx, display_name="Nora", context_window=40,
        # `<notes>` reads each anchor against this day, so it is pinned.
        now=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    ))
    found = _BLOCK.search(text)
    return found.group(0) if found else ""


CASES: dict[str, Callable[[], tuple[Note, ...]]] = {
    "one-note": lambda: (_note(1, "9 Sept dinner", "Three venues; nobody has picked."),),
    "several-notes-in-order": lambda: (
        _note(1, "Dinner", "which venue", anchor_date=20260909),
        _note(2, "Trip to Tokyo", "dates and who is flying"),
        _note(3, "Gift for Ben", "budget undecided", anchor_date=20260915),
    ),
    "no-notes": lambda: (),
    "retired-notes-never-render": lambda: (
        _note(1, "Live", "still open"),
        _note(2, "Retired", "gone", retired_at=5),
        _note(3, "Also live", "open too"),
    ),
    "markup-newline-dash-and-quote": lambda: (
        _note(1, "Gift [for] Ben: <b> — & \"q\"", "line one\nline two & <i>x</i> — dash"),
        _note(2, "Plain", "é café — ok"),
    ),
    "an-anchor-in-every-distance": lambda: tuple(
        _note(i, f"Note {i}", f"d{i}", anchor_date=day)
        for i, day in enumerate(
            (20260909, 20260910, 20260908, 20260915, 20260901, 20250101), 1,
        )
    ),
}


def test_the_tag_turns_notes_block_is_byte_identical_to_master():
    for name, make in CASES.items():
        assert _block(make()) == GOLDEN[name], name


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) == set(GOLDEN)


def test_the_cases_vary_what_the_opt_in_could_disturb():
    """Every case's golden holds a positional `n` (so a switch of default would
    show), the empty set holds nothing, and a retired note is absent."""
    assert 'n="1"' in GOLDEN["one-note"]
    assert 'n="3"' in GOLDEN["several-notes-in-order"]
    assert GOLDEN["no-notes"] == ""
    assert "Retired" not in GOLDEN["retired-notes-never-render"]
    assert 'n="2"' in GOLDEN["retired-notes-never-render"]
    assert "0191f2c4" not in "".join(GOLDEN.values())
