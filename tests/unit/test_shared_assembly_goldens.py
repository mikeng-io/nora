"""The shared assembly builders render byte-identical to what each occasion built for
itself.

**The goldens in `tests/fixtures/context_assembly_step1_goldens.json` were
captured from the code BEFORE it was refactored**, over synthetic fixtures, and
are the criterion. What is compared is the whole rendered text each occasion
sends, not a substring of it:

* `assemble` — its default output on blocks including an empty-body one, and the
  `sep=` form against the hand join each Dreamer adaptor writes today
  (`"\\n\\n".join` and `"\\n".join`).
* the one-line `<today>` block, including the `str(today)` fallback for a day
  that is not a date.
* the row-id window, where `n` is the REAL row id and each caller keeps its own
  attribute set and order.

Nothing here regenerates a golden. Changing one is a model-visible change and
belongs in a step that says so.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from nora.domain.blocks import (
    ContextBlock,
    assemble,
    render_row_window,
    row_speaker,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.note_render import today_block

GOLDEN: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "context_assembly_step1_goldens.json")
    .read_text(encoding="utf-8"),
)


def _blocks() -> list[ContextBlock]:
    """Out of `order` on purpose, with one empty body, and bodies carrying the
    characters a renderer must leave alone (`assemble` renders; it never
    escapes)."""
    return [
        ContextBlock(name="notes", body="n body", order=20),
        ContextBlock(name="today", body="2026-10-01 Thu", order=10),
        ContextBlock(name="events", body="", order=15),
        ContextBlock(name="standing", body="a & b <c>\nline2", order=40),
    ]


def _chosen(blocks: list[ContextBlock]) -> list[ContextBlock]:
    return sorted((b for b in blocks if b.body), key=lambda b: b.order)


# --- (a) assemble ------------------------------------------------------------


def test_assemble_without_a_separator_is_byte_identical_to_master():
    assert assemble(_blocks()) == GOLDEN["assemble-default"]
    assert assemble(_blocks(), sep=None) == GOLDEN["assemble-default"]
    assert assemble([]) == GOLDEN["assemble-empty"] == ""


def test_assemble_with_a_separator_equals_the_hand_join_each_adaptor_writes():
    blocks = _blocks()
    assert assemble(blocks, sep="\n\n") == GOLDEN["assemble-handjoin-nn"]
    assert assemble(blocks, sep="\n") == GOLDEN["assemble-handjoin-n"]
    # The same thing the adaptors write by hand, computed rather than recalled.
    assert assemble(blocks, sep="\n\n") == "\n\n".join(b.render() for b in _chosen(blocks))
    assert assemble(blocks, sep="\n") == "\n".join(b.render() for b in _chosen(blocks))


def test_a_separator_joins_and_does_not_terminate():
    """The default closes every block with a newline; a separator goes BETWEEN
    blocks, so the text ends on the last block's closing tag."""
    blocks = _blocks()
    assert assemble(blocks).endswith("</standing>\n")
    assert assemble(blocks, sep="\n\n").endswith("</standing>")
    assert assemble([blocks[2]], sep="\n\n") == ""  # an empty body is dropped


def test_ordering_by_order_holds_with_a_separator():
    blocks = _blocks()
    rendered = assemble(blocks, sep="\n\n")
    positions = [rendered.index(f"<{name} ") for name in ("today", "notes", "standing")]
    assert positions == sorted(positions)
    assert "<events" not in rendered
    assert assemble(list(reversed(blocks)), sep="\n\n") == rendered


# --- (b) the one-line <today> block -----------------------------------------


def test_the_shared_today_builder_renders_what_the_three_prompts_carry():
    day = date(2026, 10, 1)
    assert today_block(day).render() == GOLDEN["curator-now-20261001"]
    assert today_block("20261345").render() == GOLDEN["curator-now-20261345"]
    # order and key are the caller's own, and are the only things that vary.
    stamped = today_block(day)
    assert (stamped.order, stamped.key) == (0, "turn")


# --- (c) the row-id window ---------------------------------------------------


_NOW_MS = 1_755_000_000_000


def _candidate_rows() -> tuple[TranscriptRow, ...]:
    return (
        TranscriptRow(
            id=41, sender_id="p1", sender_name="Ada", ts=_NOW_MS,
            body="Sai Kung <b>& co</b>\nsecond line", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None, person_name="Ada Walsh",
        ),
        TranscriptRow(
            id=42, sender_id="p2", sender_name="x]: y", ts=_NOW_MS + 60_000,
            body=None, is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None, media_json='[{"k":1}]', quoted_ts=_NOW_MS,
        ),
        TranscriptRow(
            id=43, sender_id="+85291234567", sender_name=" ",
            ts=_NOW_MS + 120_000, body="hi", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )


def test_the_shared_window_prepends_the_real_row_id_and_keeps_the_callers_attrs():
    """What the two passes share and nothing more: tag `m`, `n` is the row's own
    id (never a position), then THE CALLER'S attributes in the caller's order,
    escaped, a `None` dropping its attribute."""
    rows = _candidate_rows()
    rendered = render_row_window(
        rows,
        row_id=lambda r: r.id,
        attrs=lambda r: (("from", row_speaker(r)), ("room", "R & <1>"), ("gone", None)),
        body=lambda r: r.body,
    )
    assert rendered.splitlines()[0] == (
        '<m n="41" from="Ada Walsh" room="R &amp; &lt;1&gt;">Sai Kung &lt;b&gt;&amp; co&lt;/b&gt;'
    )
    assert '<m n="42" from="x]: y" room="R &amp; &lt;1&gt;"></m>' in rendered
    # The raw account id never reaches a prompt (R-18): an unmapped, blank name
    # is `unknown`, not the E.164.
    assert '<m n="43" from="unknown" room="R &amp; &lt;1&gt;">hi</m>' in rendered
    assert "+852" not in rendered
    assert render_row_window(
        (), row_id=lambda r: r.id, attrs=lambda r: (), body=lambda r: r.body,
    ) == ""
