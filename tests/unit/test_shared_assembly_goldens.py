"""Step 1 of `design/context-assembly/04-every-occasion.md` §5: the builders the
occasions each wrote for themselves become shared ones, and NOT ONE BYTE any
model reads changes.

**The goldens in `tests/fixtures/context_assembly_step1_goldens.json` were
captured from the code BEFORE it was refactored** (`master` at `db8ae781`), over
synthetic fixtures, and are the criterion. A step declared byte-identical that is
not is a defect, so what is compared is the whole rendered text each occasion
sends, not a substring of it:

* `assemble` — its default output on blocks including an empty-body one, and the
  `sep=` form against the hand join each Dreamer adaptor writes today
  (`"\\n\\n".join` and `"\\n".join`).
* the one-line `<today>` block — through note-merge's, note-candidates' and the
  curator's own prompts, including the curator's `str(today)` fallback for a day
  that is not a date.
* the row-id window — note-candidates' `<m>` lines and the curator's `evidence`
  and `own_sends` blocks, where `n` is the REAL row id and each pass keeps its own
  attribute set and order.

Nothing here regenerates a golden. Changing one is a model-visible change and
belongs in a step that says so.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nora.domain.blocks import (
    ContextBlock,
    assemble,
    render_row_window,
    row_speaker,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.note_candidates import _render_line
from nora.language.note_curator import _now_block, _render_evidence, _render_own_sends
from nora.language.note_render import today_block
from nora.testing.fakes import FakeRecordStore
from nora.testing.note_store import InMemoryNoteStore
from tests.unit import test_note_candidates as cand
from tests.unit import test_note_curator as cur
from tests.unit import test_note_merge as merge

GOLDEN: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "context_assembly_step1_goldens.json")
    .read_text(encoding="utf-8"),
)

_TODAY_BLOCK = re.compile(r"<today [^>]*>\n[^\n]*\n</today>")


def _blocks() -> list[ContextBlock]:
    """Out of `order` on purpose, with one empty body, and bodies carrying the
    characters a renderer must leave alone (`assemble` renders; it never
    escapes)."""
    return [
        ContextBlock(name="note", body="n body", order=20),
        ContextBlock(name="today", body="2026-10-01 Thu", order=10),
        ContextBlock(name="evidence", body="", order=15),
        ContextBlock(name="own_sends", body="a & b <c>\nline2", order=40),
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
    assert assemble(blocks).endswith("</own_sends>\n")
    assert assemble(blocks, sep="\n\n").endswith("</own_sends>")
    assert assemble([blocks[2]], sep="\n\n") == ""  # an empty body is dropped


def test_ordering_by_order_holds_with_a_separator():
    blocks = _blocks()
    rendered = assemble(blocks, sep="\n\n")
    positions = [rendered.index(f"<{name} ") for name in ("today", "note", "own_sends")]
    assert positions == sorted(positions)
    assert "<evidence" not in rendered
    assert assemble(list(reversed(blocks)), sep="\n\n") == rendered


# --- (b) the one-line <today> block -----------------------------------------


async def test_the_merge_prompt_is_byte_identical_to_master():
    store = InMemoryNoteStore()
    await merge._note(store, "Saturday dinner", at=merge.T0 - 3 * merge.DAY, anchor=20260927)
    await merge._note(store, "Neway booking", at=merge.T0 - 2 * merge.HOUR, anchor=None)
    prompt = await merge._adaptor(store).inputs(key=merge.WS, batch=())
    assert prompt == GOLDEN["merge-inputs"]


async def test_the_candidates_prompt_is_byte_identical_to_master():
    adaptor, store = cand._adaptor()
    await cand._open_note(store, "Wedding venue", "## Decided\n## Open\n- which venue [7]")
    prompt = await adaptor.inputs(key=cand.SCOPE, batch=_candidate_rows())
    assert prompt == GOLDEN["candidates-inputs"]
    # The block that carries order and key in this pass, as its own piece.
    assert _TODAY_BLOCK.search(prompt).group(0) == _TODAY_BLOCK.search(  # type: ignore[union-attr]
        GOLDEN["candidates-inputs"],
    ).group(0)


async def test_the_curator_prompt_is_byte_identical_to_master():
    store = InMemoryNoteStore()
    await cur._seed(store, title="Route", anchor=None, entities=("miles",),
                    revisit_at=cur.NOW - 1)
    await cur._seed(store, title="Dinner", anchor=20260909, body="Nobody booked.")
    record = FakeRecordStore()
    record.rows.append(cur._row(7, "969P from Tin Shui Chi", ts=cur.NOW + 1, is_nora=True))
    record.rows.append(cur._row(8, "thanks", ts=cur.NOW + 2))
    prompt = await cur._curator(store, record=record).inputs(key=cur.WS, batch=())
    assert prompt == GOLDEN["curator-inputs"]


def test_the_curator_today_block_is_byte_identical_including_its_fallback():
    for day in (20260910, 20261001, 20261345, 0, 99999999):
        assert _now_block(day).render() == GOLDEN[f"curator-now-{day}"], day
    # `20261345` is not a date (month 13): the block says the number, as it did.
    assert ">\n20261345\n<" in _now_block(20261345).render()


def test_the_shared_today_builder_renders_what_the_three_prompts_carry():
    day = date(2026, 10, 1)
    assert today_block(day).render() == GOLDEN["curator-now-20261001"]
    assert today_block("20261345").render() == GOLDEN["curator-now-20261345"]
    # order and key are the caller's own, and are the only things that vary.
    stamped = today_block(day)
    assert (stamped.order, stamped.key) == (0, "turn")


# --- (c) the row-id window ---------------------------------------------------


def _candidate_rows() -> tuple[TranscriptRow, ...]:
    return (
        TranscriptRow(
            id=41, sender_id="p1", sender_name="Ada", ts=cand.NOW,
            body="Sai Kung <b>& co</b>\nsecond line", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None, person_name="Ada Walsh",
        ),
        TranscriptRow(
            id=42, sender_id="p2", sender_name="x]: y", ts=cand.NOW + 60_000,
            body=None, is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None, media_json='[{"k":1}]', quoted_ts=cand.NOW,
        ),
        TranscriptRow(
            id=43, sender_id="+85291234567", sender_name=" ",
            ts=cand.NOW + 120_000, body="hi", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )


def test_every_candidates_line_is_byte_identical_to_master():
    for zone in ("Asia/Hong_Kong", "UTC"):
        for row in _candidate_rows():
            got = _render_line(row, room="Dinner & <co>", tz=ZoneInfo(zone))
            assert got == GOLDEN[f"candidates-line-{zone.split('/')[-1]}-{row.id}"], (zone, row.id)
    # No moment, no `at`: the attribute drops rather than rendering empty.
    bare = TranscriptRow(
        id=44, sender_id="p", sender_name="Bo", ts=0, body="z", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    assert _render_line(bare, room="R", tz=ZoneInfo("UTC")) == GOLDEN["candidates-line-ts0"]


_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
_MS = int(_NOW.timestamp() * 1000)


def _evidence_rows() -> tuple[TranscriptRow, ...]:
    return (
        TranscriptRow(
            id=7, sender_id="+1", sender_name="Miles", ts=_MS - 3 * 3600_000,
            body="I'll sort it\n<x>&", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None, person_name="Miles",
        ),
        TranscriptRow(
            id=9, sender_id="+2", sender_name=" ", ts=0, body="no ts",
            is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
        TranscriptRow(
            id=10, sender_id="+3", sender_name="a]: b", ts=_MS - 60_000, body=None,
            is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )


def _own_sends() -> tuple[TranscriptRow, ...]:
    return (
        TranscriptRow(
            id=11, sender_id="nora", sender_name=None, ts=_MS - 120_000,
            body="969P from <TST>", is_nora=True,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )


def test_the_curator_evidence_and_own_sends_are_byte_identical_to_master():
    ev, own = _evidence_rows(), _own_sends()
    assert _render_evidence(ev, "present", now=_NOW) == GOLDEN["curator-evidence-present"]
    assert _render_evidence(ev, "present", now=_NOW, own_sends=own, cut_short=True) == (
        GOLDEN["curator-evidence-present-own-cut"]
    )
    assert _render_evidence((), "absent", now=_NOW) == GOLDEN["curator-evidence-none-absent"]
    assert _render_evidence((), "self_only", now=_NOW, own_sends=own) == (
        GOLDEN["curator-evidence-none-selfonly-own"]
    )
    assert _render_own_sends(own, now=_NOW) == GOLDEN["curator-own-sends"]
    assert _render_own_sends((), now=_NOW) == GOLDEN["curator-own-sends-empty"] == ""


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
