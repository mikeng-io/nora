"""`Standing` and `<standing>` — the domain type's two caps and the block's
own framing.
"""

from __future__ import annotations

import pytest

from nora.domain.standing import (
    DEFAULT_STANDING_MAX_CHARS,
    STANDING_BODY_CEILING_CHARS,
    WORKING_SET_CEILING_ITEMS,
    Standing,
    WorkingSetItem,
)


def _standing(body: str = "They are picking a restaurant.") -> Standing:
    return Standing(platform="signal", conversation_id="g1", body=body,
                    fold_count=1, updated_at=1_756_000_000_000)


def test_the_operating_cap_sits_below_the_database_ceiling():
    """The two layers, asserted as a RELATION rather than as two numbers.

    Either constant can move — the operating cap is config's default and the
    ceiling is DDL — but the ordering is the thing that makes the pair
    coherent: an operating cap above the ceiling would mean every fold the sink
    accepted was refused by Postgres, which is a config error discovered by a
    production turn rather than at startup.
    """
    assert DEFAULT_STANDING_MAX_CHARS < STANDING_BODY_CEILING_CHARS


def test_a_body_over_the_ceiling_is_unrepresentable():
    """The domain half of the two-place enforcement. It is the CEILING that
    lives here, not the operating cap: the operating cap is a knob and a knob
    cannot be a constructor invariant, while the ceiling is the bound no writer
    may pass and belongs where the object is made."""
    with pytest.raises(ValueError, match="ceiling"):
        _standing("x" * (STANDING_BODY_CEILING_CHARS + 1))


def test_a_body_exactly_at_the_ceiling_is_accepted():
    """The boundary, from the other side — an off-by-one that refused the
    ceiling itself would make the database CHECK and the type disagree about
    one value, which is the hardest kind of drift to find."""
    assert len(_standing("x" * STANDING_BODY_CEILING_CHARS).body) == (
        STANDING_BODY_CEILING_CHARS
    )


@pytest.mark.parametrize("body", ["", "   ", "\n\t "])
def test_a_blank_body_is_refused(body):
    """A position that says nothing is not a position. Whitespace counts as
    blank, which is `_required`'s own hole one tier over: `'   '` is truthy to
    `if not x`, and the database says `length(btrim(body)) > 0` for exactly
    that reason."""
    with pytest.raises(ValueError, match="needs a body"):
        _standing(body)


def test_a_negative_fold_count_is_refused():
    with pytest.raises(ValueError, match="never negative"):
        Standing(platform="signal", conversation_id="g1", body="x",
                 fold_count=-1, updated_at=1)


def test_the_row_id_watermark_is_a_row_id_or_none():
    """The fold's row-id watermark.
    `None` means "never folded — read from the beginning", so `0` would
    be a second spelling of that state that compares unequal to it — and
    messages ids start at 1 anyway. A watermark satisfiable by accident is how
    a conversation re-reads from the beginning on every fold.
    """
    assert Standing(platform="signal", conversation_id="g1", body="x",
                    fold_count=1, updated_at=1).last_folded_row_id is None
    ok = Standing(platform="signal", conversation_id="g1", body="x",
                  fold_count=1, updated_at=1, last_folded_row_id=42)
    assert ok.last_folded_row_id == 42
    with pytest.raises(ValueError, match="row id or None"):
        Standing(platform="signal", conversation_id="g1", body="x",
                 fold_count=1, updated_at=1, last_folded_row_id=0)
    with pytest.raises(ValueError, match="row id or None"):
        Standing(platform="signal", conversation_id="g1", body="x",
                 fold_count=1, updated_at=1, last_folded_row_id=-1)










# --- the sectioned schema: `hold_open` ("I hold open") and `i_owe` ("I owe") ---


def test_hold_open_and_i_owe_default_to_none():
    """The correct reading of every pre-existing row: additive
    columns, no backfill — `nora.infra.sql.migrations.versions.
    0076_standing_sections`'s own argument."""
    standing = _standing()
    assert standing.hold_open is None
    assert standing.i_owe is None


def test_a_blank_hold_open_or_i_owe_is_refused():
    """`None` is "not revised"; a blank string is a second spelling of the
    same absence and is refused rather than accepted as content —
    `WorkingSetItem.bears_on`'s own rule, one section over."""
    with pytest.raises(ValueError, match="hold_open"):
        Standing(platform="signal", conversation_id="g1", body="x",
                  fold_count=1, updated_at=1, hold_open="   ")
    with pytest.raises(ValueError, match="i_owe"):
        Standing(platform="signal", conversation_id="g1", body="x",
                  fold_count=1, updated_at=1, i_owe="")


def test_a_section_over_the_ceiling_is_unrepresentable():
    with pytest.raises(ValueError, match="hold_open"):
        Standing(platform="signal", conversation_id="g1", body="x",
                  fold_count=1, updated_at=1,
                  hold_open="a" * (STANDING_BODY_CEILING_CHARS + 1))








# --- working_set ---

_NOW_MS = 1_756_000_000_000


def _item(
    what: str = "a second restaurant the search returned",
    source: str = "web_search",
    found_at: int = _NOW_MS,
    bears_on: str | None = "where to eat",
) -> WorkingSetItem:
    return WorkingSetItem(what=what, source=source, found_at=found_at, bears_on=bears_on)


def test_a_working_set_item_needs_a_what_and_a_source():
    with pytest.raises(ValueError, match="needs a what"):
        WorkingSetItem(what="", source="web_search", found_at=1)
    with pytest.raises(ValueError, match="needs a source"):
        WorkingSetItem(what="a place", source="   ", found_at=1)


def test_a_working_set_item_found_at_is_never_negative():
    with pytest.raises(ValueError, match="never negative"):
        WorkingSetItem(what="a place", source="web_search", found_at=-1)


def test_a_working_set_over_the_item_ceiling_is_unrepresentable():
    """`working_set`'s own version of `test_a_body_over_the_ceiling_is_
    unrepresentable` — the ABSOLUTE ceiling, asserted in `__post_init__` here
    and again by `standing_working_set_fits_the_ceiling_check` in the
    database (migration 0065)."""
    over = tuple(
        _item(what=f"item {i}", found_at=_NOW_MS) for i in range(WORKING_SET_CEILING_ITEMS + 1)
    )
    with pytest.raises(ValueError, match="working_set holds"):
        Standing(platform="signal", conversation_id="g1", body="x",
                 fold_count=1, updated_at=1, working_set=over)


def test_a_working_set_at_the_item_ceiling_is_accepted():
    """The boundary, from the other side — `test_a_body_exactly_at_the_
    ceiling_is_accepted`'s own reason: an off-by-one here would make the
    domain type and the database CHECK disagree about one value."""
    at = tuple(
        _item(what=f"item {i}", found_at=_NOW_MS) for i in range(WORKING_SET_CEILING_ITEMS)
    )
    assert len(Standing(
        platform="signal", conversation_id="g1", body="x",
        fold_count=1, updated_at=1, working_set=at,
    ).working_set) == WORKING_SET_CEILING_ITEMS








