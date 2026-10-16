"""The note tier's domain invariants, expressed as a SHAPE rather than as prose.

Four fields are load-bearing, and three of them only if the wrong thing is
*unrepresentable*:

- **a note with no closing condition is not a note** — it is either a fact
  (settled) or ignorance (nothing would resolve it). This is the discriminator
  that stops the tier swallowing the other three tiers, so it is a constructor
  refusal, not a docstring;
- **a note with no anchor is not a note** — "we should hang out sometime" has no
  referent anything could later be looked up by. The anchor is what merge
  eligibility and mechanical retirement are computed from, so a
  note without one breaks both;
- **the injected entry is capped at construction, never truncated at render** —
  Truncating in the renderer is silent loss; a constructor that refuses is
  not.

The supersession rule is deliberately WEAKER than the fact tier's, and that
difference is the whole reason `DreamSink.supersede` takes
`replaced_by: str | None` (DEC-130). `fact` asserts a biconditional
— `(superseded_by is null) = (superseded_at is null)` — because a contradicted
fact is always replaced by the corrected one. A note is not: **retirement has no
successor.** A dinner that happened is replaced by nothing. So this tier asserts
the *implication* only — a replacement implies a retirement, never the converse
— and `test_a_note_may_retire_with_no_successor` is the test that would fail if
someone "tidied" it into the fact tier's biconditional.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import pathlib

import pytest

from nora.domain.notes import (
    BODY_MAX_CHARS,
    DESCRIPTION_MAX_CHARS,
    TAG_PATH_PREFIX,
    TITLE_MAX_CHARS,
    Note,
    NoteRevision,
)

NOW = 1_755_100_000_000


def _note(**over) -> Note:
    kwargs: dict = {
        "id": "01930000-0000-7000-8000-000000000001",
        "unit": "friends",
        "title": "Dinner, 9 Sept",
        "description": "Three candidate restaurants, nobody has decided yet.",
        "uncertainty": "which restaurant, and whether it happens at all",
        "closing_condition": "somebody picks a place, or the date passes",
        "anchor_date": 20260909,
        "anchor_entity_ids": (),
        "established_in": "signal:MP4ODQOAWL",
        "created_at": NOW,
        "head_revision_id": "01930000-0000-7000-8000-0000000000aa",
    }
    kwargs.update(over)
    return Note(**kwargs)


# ---------------------------------------------------------------------------
# the closing condition — the discriminator
# ---------------------------------------------------------------------------


def test_a_note_needs_a_closing_condition():
    """Without one there is no decidable answer to «has this resolved?», so
    retirement, promotion-readiness and a revisit's success criterion all lose
    their input at once."""
    with pytest.raises(ValueError, match="closing condition"):
        _note(closing_condition="")


def test_a_closing_condition_of_whitespace_is_refused():
    """`'   '` is non-empty to `if not x` only if you forget to strip. It is the
    same hole `fact_model_is_named_check` closes with `btrim`."""
    with pytest.raises(ValueError, match="closing condition"):
        _note(closing_condition="   ")


# ---------------------------------------------------------------------------
# the anchor — what merge and retirement are computed from
# ---------------------------------------------------------------------------


def test_a_note_needs_an_anchor():
    """"We should hang out sometime" has no referent. It dies as a candidate row
    nobody sees, never as a note."""
    with pytest.raises(ValueError, match="anchor"):
        _note(anchor_date=None, anchor_entity_ids=())


def test_a_date_alone_is_a_valid_anchor():
    note = _note(anchor_date=20260909, anchor_entity_ids=())
    assert note.anchor_date == 20260909


def test_entities_alone_are_a_valid_anchor():
    """«what are we playing next» has no date. It takes the strike-based
    ageing path instead of expiry, but it is a perfectly good note."""
    note = _note(anchor_date=None, anchor_entity_ids=("e-zone42",))
    assert note.anchor_entity_ids == ("e-zone42",)


def test_both_together_are_a_valid_anchor():
    note = _note(anchor_date=20261114, anchor_entity_ids=("e-grant",))
    assert note.anchor_date == 20261114
    assert note.written_anchor_entities == ("e-grant",)


def test_the_written_entities_are_readable_but_are_not_a_key():
    """`note.anchor_entity_ids` is what the model handed `note_create`,
    verbatim and unresolved, and a key built from it is wrong in both
    directions: three spellings of one wedding are three keys, and two
    unrelated notes that both say 「wedding」 are one. Nothing reads it as a key.

    Asserted as an ABSENCE with a positive control, because a property that does
    not exist cannot be called by accident."""
    note = _note(anchor_date=20261114, anchor_entity_ids=("wedding",))

    assert not hasattr(note, "anchor_key")
    assert note.written_anchor_entities == ("wedding",), (
        "positive control: the written words are still readable, just not a "
        "key"
    )


# ---------------------------------------------------------------------------
# the injected entry — capped at construction, never truncated at render
# ---------------------------------------------------------------------------


def test_a_title_over_the_cap_is_refused():
    with pytest.raises(ValueError, match="title"):
        _note(title="x" * (TITLE_MAX_CHARS + 1))


def test_a_description_over_the_cap_is_refused():
    with pytest.raises(ValueError, match="description"):
        _note(description="x" * (DESCRIPTION_MAX_CHARS + 1))


def test_a_title_at_the_cap_is_accepted():
    assert _note(title="x" * TITLE_MAX_CHARS).title


def test_an_empty_title_is_refused():
    """The index line is the only thing standing between a note and being
    invisible. An untitled entry is an entry nobody can reach for."""
    with pytest.raises(ValueError, match="title"):
        _note(title="  ")


def test_an_empty_description_is_refused():
    """`description` is the when-to-reach-for-this line, the skills-loader shape
    Without it the index says a note exists and nothing about
    when it matters."""
    with pytest.raises(ValueError, match="description"):
        _note(description="")


# ---------------------------------------------------------------------------
# supersession — the implication, NOT the fact tier's biconditional
# ---------------------------------------------------------------------------


def test_a_standing_note_has_neither_retirement_nor_successor():
    note = _note()
    assert note.is_standing
    assert note.retired_at is None
    assert note.replaced_by is None


def test_a_note_may_retire_with_no_successor():
    """**The difference from the fact tier, and the reason DEC-130's
    `replaced_by` is nullable at the port.** A dinner that happened is replaced
    by nothing. Collapsing this into `fact`'s biconditional would force this
    tier to invent a placeholder id — the invent-a-value-to-satisfy-a-schema
    move."""
    note = _note(retired_at=NOW + 1, replaced_by=None)
    assert not note.is_standing


def test_a_retirement_may_name_a_successor():
    note = _note(retired_at=NOW + 1, replaced_by="01930000-0000-7000-8000-00000000000f")
    assert not note.is_standing
    assert note.replaced_by


def test_a_successor_without_a_retirement_is_refused():
    """The one direction that IS incoherent: a note replaced by another but
    still standing would render in the index beside its own replacement."""
    with pytest.raises(ValueError, match="retired"):
        _note(retired_at=None, replaced_by="01930000-0000-7000-8000-00000000000f")


# ---------------------------------------------------------------------------
# revisions — append-only, and the head is what CAS compares
# ---------------------------------------------------------------------------


def test_the_first_revision_has_no_parent():
    rev = NoteRevision(
        id="r1", note_id="n1", body="the three places",
        parent_revision_id=None, written_at=NOW, established_by="miles_novak",
    )
    assert rev.parent_revision_id is None


def test_a_later_revision_names_the_head_it_was_written_against():
    """The CAS: the parent IS the head the writer read. A revision that
    cannot name what it was written against cannot be refused when the head
    moved under it."""
    rev = NoteRevision(
        id="r2", note_id="n1", body="booked Ho Lee Fook",
        parent_revision_id="r1", written_at=NOW + 1, established_by="miles_novak",
    )
    assert rev.parent_revision_id == "r1"


def test_a_revision_needs_a_body():
    with pytest.raises(ValueError, match="body"):
        NoteRevision(
            id="r1", note_id="n1", body="   ",
            parent_revision_id=None, written_at=NOW, established_by="miles_novak",
        )


def test_a_revision_names_who_wrote_it():
    """`established_by` is what the strike reset reads — strikes reset only
    on a TAG-PATH revision, so a revision that cannot say who wrote it makes the
    loop breaker self-referential and therefore decorative."""
    with pytest.raises(ValueError, match="established_by"):
        NoteRevision(
            id="r1", note_id="n1", body="x",
            parent_revision_id=None, written_at=NOW, established_by="",
        )


def test_a_revision_is_frozen():
    """Append-only means the stored revision cannot be edited in place; a frozen
    dataclass is what makes «append a revision» the only available verb."""
    rev = NoteRevision(
        id="r1", note_id="n1", body="x",
        parent_revision_id=None, written_at=NOW, established_by="miles_novak",
    )
    with pytest.raises((AttributeError, TypeError)):
        rev.body = "y"  # type: ignore[misc]


# --- the body cap, and why there are TWO numbers ----------------------------


def test_a_body_over_the_operating_cap_is_refused():
    """The body is NOT injected — it is read on demand — so the exposure is the
    CURATOR, whose per-tick budget is counted in ROWS. A row budget is only a
    budget if a row has a bounded size: one unbounded body eats a whole tick's
    allowance, and the pass then reports «nothing to do» about the notes it
    never reached."""
    with pytest.raises(ValueError, match="at most 8000"):
        NoteRevision(
            id="r1", note_id="n1", body="x" * (BODY_MAX_CHARS + 1),
            parent_revision_id=None, written_at=1, established_by="tag:miles",
        )


def test_a_body_exactly_at_the_cap_is_accepted():
    """The cap is inclusive. An off-by-one here would refuse the longest
    legitimate arrangement, which is the case the number was chosen for."""
    revision = NoteRevision(
        id="r1", note_id="n1", body="x" * BODY_MAX_CHARS,
        parent_revision_id=None, written_at=1, established_by="tag:miles",
    )

    assert len(revision.body) == BODY_MAX_CHARS


def test_the_tag_path_prefix_has_no_second_copy_in_its_producer():
    """**The strike reset's correctness rests on two strings matching**:
    `TAG_PATH_PREFIX` is defined here and `nora/agent/tools.py` — the SOLE
    producer — must import it rather than write the literal `f"tag:{...}"`.

    Otherwise nothing fails on a rename. `is_tag_path_revision` would answer
    False for every revision a person wrote, every note would look
    background-authored, and the strike breaker would march every actively-worked
    note to its strike ceiling — silently, because a strike has no log line.

    A source check rather than a type check, because `nora/agent` is outside
    `make gate`'s mypy scope: the import is only load-bearing if nothing
    re-types the literal beside it.
    """
    source = pathlib.Path("nora/agent/tools.py").read_text()

    assert "TAG_PATH_PREFIX" in source, (
        "positive control: the producer imports the constant at all"
    )
    assert f'f"{TAG_PATH_PREFIX}{{' not in source, (
        "the producer re-typed the prefix instead of importing it"
    )
