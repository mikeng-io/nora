"""`parse_note_body`.

One test per refusal rule, each on a body that breaks that rule and nothing
else, so a refusal that fires for the wrong reason fails too: every test
asserts the RULE, not merely that something was refused. Each refusal has a
positive control beside it (the same body, repaired), so a validator that
refused everything would fail the controls. The rules are falsified one by
one in `scripts/mutations/note-body.json`.

Every name, id and fact here is invented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nora.domain.memo import citations_of
from nora.domain.note_body import (
    RULES,
    NoteBodyError,
    Shown,
    ShownMessage,
    citations_in,
    cited_ids_in,
    parse_note_body,
    rollup_key,
    rollup_keys_in,
)

SHOWN = Shown(
    rendered=(
        ShownMessage(4812, "Ada", "p_ada"),
        ShownMessage(4815, "Ben", "p_ben"),
        ShownMessage(4816, "Cat", "p_cat"),
        ShownMessage(4817, "Ben", "p_ben"),
        ShownMessage(4818, "Ada", "p_ada"),
        ShownMessage(4819, "Nora", None, is_nora=True),
        ShownMessage(4820, "Dee", "p_dee"),
    ),
    roster={"Ada": "p_ada", "Ben": "p_ben", "Cat": "p_cat", "Dee": "p_dee", "Nora": "nora"},
    nora_names=frozenset({"Nora"}),
)

#: A conformant body.
CONFORMANT = """\
## Decided
- Dinner for Ada's birthday is on 2026-10-03 Sat (Ada) [4812]
- Ben and Cat are coming (Ben, Cat) [4815, 4816]
## Open
- which restaurant [4812]
- whether Dee can come after 21:00 [4820]
## Owed
- Ben -> book a table for five -> 2026-10-01 [4818]
## Proposed
- the dim sum place in Sai Kung (Ada) [4812]
- the rooftop bar in Central (Cat) [4816]
## Ruled out
- the beach club: closed for renovation until November (Ben) [4817]"""


#: A body that holds, for a test about one later section to extend.
BASE = "## Decided\n## Open\n- y [4812]\n"


def _refused(text: str, *, shown: Shown = SHOWN, require_open: bool = False) -> str:
    with pytest.raises(NoteBodyError) as err:
        parse_note_body(text, shown=shown, require_open=require_open)
    assert err.value.rule in RULES
    return err.value.rule


def test_the_reviewers_conformant_body_parses_and_attaches_person_ids():
    body = parse_note_body(CONFORMANT, shown=SHOWN, require_open=True)
    assert body.text == CONFORMANT
    assert [s.heading for s in body.sections] == [
        "Decided", "Open", "Owed", "Proposed", "Ruled out",
    ]
    assert body.cited_ids == {4812, 4815, 4816, 4817, 4818, 4820}
    # Code attaches the ids, from the author of the cited row and the roster.
    assert body.person_ids == {"p_ada", "p_ben", "p_cat"}
    owed = body.section("Owed")
    assert owed is not None and owed.lines[0].owner == "Ben"


def test_whitespace_bullets_and_heading_case_are_repaired_and_nothing_else():
    messy = (
        "## decided  \r\n"
        "* Dinner for Ada's birthday is on 2026-10-03 Sat (Ada) [4812]\r\n"
        "\r\n\r\n"
        "+ Ben and Cat are coming (Ben, Cat) [4815, 4816]   \n"
        "## OPEN\n"
        "- which restaurant [4812]\n"
    )
    body = parse_note_body(messy, shown=SHOWN)
    assert body.text == (
        "## Decided\n"
        "- Dinner for Ada's birthday is on 2026-10-03 Sat (Ada) [4812]\n"
        "- Ben and Cat are coming (Ben, Cat) [4815, 4816]\n"
        "## Open\n"
        "- which restaurant [4812]"
    )


def test_a_heading_outside_the_seven_is_refused():
    assert _refused(BASE + "## Notes\n- z [4812]") == "unknown_section"
    assert _refused("### Decided\n- x (Ada) [4812]\n## Open") == "unknown_section"


def test_a_heading_repeated_or_out_of_order_is_refused():
    assert _refused("## Open\n- y [4812]\n## Decided\n- x (Ada) [4812]") == "section_order"
    assert _refused("## Decided\n- x (Ada) [4812]\n## Decided\n## Open") == "section_order"
    # Control: the same lines in order parse.
    parse_note_body("## Decided\n- x (Ada) [4812]\n## Open\n- y [4812]", shown=SHOWN)


def test_decided_and_open_are_always_present_and_not_both_empty():
    assert _refused("## Decided\n- x (Ada) [4812]") == "missing_section"
    assert _refused("## Open\n- y [4812]") == "missing_section"
    both_empty = "## Decided\n## Open\n## Owed\n- Ben -> x -> no date given [4815]"
    assert _refused(both_empty) == "missing_section"
    # Control: either one holding a line is enough.
    parse_note_body("## Decided\n- x (Ada) [4812]\n## Open", shown=SHOWN)
    parse_note_body("## Decided\n## Open\n- y [4812]", shown=SHOWN)


def test_a_note_candidates_create_needs_an_open_corner():
    body = "## Decided\n- x (Ada) [4812]\n## Open"
    assert _refused(body, require_open=True) == "missing_section"
    # Control: a revision may say nothing is left open.
    parse_note_body(body, shown=SHOWN)


def test_text_outside_a_list_item_is_refused():
    assert _refused("a preamble\n## Decided\n- x (Ada) [4812]\n## Open") == "not_a_line"
    assert _refused("## Decided\nx, as prose (Ada) [4812]\n## Open") == "not_a_line"
    nested = "## Decided\n- x (Ada) [4812]\n  - a nested one [4812]\n## Open"
    assert _refused(nested) == "not_a_line"
    assert _refused("## Decided\n- - x (Ada) [4812]\n## Open") == "not_a_line"


def test_a_line_that_breaks_its_sections_shape_is_refused():
    assert _refused(BASE + "## Owed\n- Ben books it [4815]") == "not_a_line"
    assert _refused(
        BASE + "## Ruled out\n- the beach club (Ben) [4817]"
    ) == "not_a_line"
    assert _refused(BASE + "## Folded\n- Old note") == "not_a_line"


def test_a_line_with_no_ids_is_refused():
    assert _refused("## Decided\n- x (Ada)\n## Open") == "uncited"
    # A transcript position is mapped to an id before the body is stored.
    assert _refused("## Decided\n## Open\n- y [#4]") == "uncited"
    # Ids are ASCII digits: `\\d` would read these as 4812.
    assert _refused("## Decided\n## Open\n- y [\u0664\u0668\u0661\u0662]") == "uncited"


def test_the_code_written_sections_carry_no_ids():
    body = (
        "## Decided\n## Open\n- y [4812]\n"
        "## From memory\n- Ada prefers dim sum (as of 2026-09-20 Sun)\n"
        "## Folded\n- Birthday dinner: a place is booked (folded 2026-09-21)"
    )
    assert len(parse_note_body(body, shown=SHOWN).sections) == 4


def test_an_id_neither_shown_nor_carried_is_refused():
    body = "## Decided\n## Open\n- y [9999]"
    assert _refused(body) == "unknown_id"
    # Control: an id the head already cites is carried, not re-read.
    parse_note_body(body, shown=Shown(rendered=SHOWN.rendered, carried=frozenset({9999})))


def test_a_line_citing_noras_own_message_is_refused():
    assert _refused("## Decided\n## Open\n- y [4812, 4819]") == "cites_nora"


def test_a_name_that_wrote_none_of_the_cited_messages_is_refused():
    assert _refused("## Decided\n- x (Cat) [4815]\n## Open") == "misattributed"
    # Control: the author of the cited message.
    parse_note_body("## Decided\n- x (Ben) [4815]\n## Open", shown=SHOWN)
    carried = Shown(rendered=SHOWN.rendered, carried=frozenset({9999}), nora_names=SHOWN.nora_names)
    # A line citing only a carried id names an author this write never saw.
    parse_note_body("## Decided\n- x (Eve) [9999]\n## Open", shown=carried)
    # One rendered id is enough to check against, and Nora is never an author.
    assert _refused("## Decided\n- x (Eve) [4815, 9999]\n## Open", shown=carried) == "misattributed"
    assert _refused("## Decided\n- x (Nora) [9999]\n## Open", shown=carried) == "misattributed"


def test_a_missing_or_first_name_attribution_is_completed_only_when_one_person_can_have_said_it():
    """Who wrote a cited rendered message is code's fact: a line that leaves
    `(<names>)` out while citing ONE sender, or writes a first name exactly one
    cited sender extends, is completed from the rendered `from`. Several cited
    senders stay a refusal that lists them; so does a name the slot cannot hold."""
    full = Shown(rendered=(
        ShownMessage(5001, "Tyler Cortez", "p_tyler"),
        ShownMessage(5002, "Avery Walsh", "p_avery"),
        ShownMessage(5003, "Tyler Lee", "p_tlee"),
        ShownMessage(5004, "Tyler Cortez", "p_tyler"),
        ShownMessage(5005, "Amy (work)", "p_amy"),
        ShownMessage(5006, "Cortez, Tyler", "p_ct"),
    ))
    body = parse_note_body("## Decided\n- DLC bought [5001, 5004]\n## Open", shown=full)
    line = body.sections[0].lines[0]
    assert line.text == "DLC bought (Tyler Cortez) [5001, 5004]"
    assert line.person_ids == ("p_tyler",)
    short = parse_note_body("## Decided\n- DLC bought (Tyler) [5001]\n## Open", shown=full)
    assert short.sections[0].lines[0].text == "DLC bought (Tyler Cortez) [5001]"
    # A first name beside the full one is one person, named once.
    both = parse_note_body("## Decided\n- x (Tyler, Tyler Cortez) [5001]\n## Open", shown=full)
    assert both.sections[0].lines[0].person_ids == ("p_tyler",)
    # Two cited senders: who said it is the model's call, and the refusal names them.
    with pytest.raises(NoteBodyError, match="among: Tyler Cortez, Avery Walsh"):
        parse_note_body("## Decided\n- DLC bought [5001, 5002]\n## Open", shown=full)
    # Two cited Tylers: the first name is ambiguous, so it is still refused.
    assert _refused("## Decided\n- x (Tyler) [5001, 5003]\n## Open", shown=full) == "misattributed"
    # A sender the slot cannot hold is never filled in.
    assert _refused("## Decided\n- x [5005]\n## Open", shown=full) == "misattributed"
    assert _refused("## Decided\n- x [5006]\n## Open", shown=full) == "misattributed"
    # Carried ids alone cannot fill it: nothing rendered says who wrote them.
    carried = Shown(rendered=SHOWN.rendered, carried=frozenset({9999}), nora_names=SHOWN.nora_names)
    assert _refused("## Decided\n- x [9999]\n## Open", shown=carried) == "misattributed"


def test_an_owed_owner_outside_the_roster_or_nora_is_refused():
    assert _refused(BASE + "## Owed\n- Zed -> book -> no date given [4815]") == "unknown_owner"
    assert _refused(BASE + "## Owed\n- Nora -> book -> no date given [4815]") == "unknown_owner"
    # Control: an owner who never spoke, named from the roster.
    parse_note_body(BASE + "## Owed\n- Dee -> book -> when the venue answers [4815]", shown=SHOWN)


def test_an_impossible_or_malformed_date_or_a_wrong_weekday_is_refused():
    assert _refused("## Decided\n- dinner on 2026-02-30 (Ada) [4812]\n## Open") == "bad_date"
    assert _refused("## Decided\n- dinner on 2026-10-03 Sun (Ada) [4812]\n## Open") == "bad_date"
    assert _refused("## Decided\n- on 2026-10-03 Saturday (Ada) [4812]\n## Open") == "bad_date"
    assert _refused("## Decided\n- dinner on 2026-9-27 (Ada) [4812]\n## Open") == "bad_date"
    assert _refused(BASE + "## Owed\n- Ben -> book -> tomorrow [4815]") == "bad_date"
    assert _refused(BASE + "## Owed\n- Ben -> book -> 2026-10-01 25:00 [4815]") == "bad_date"
    assert _refused(BASE + "## From memory\n- Ada likes dim sum (as of 20260920)") == "bad_date"
    # Control: the right weekday, and a due with a time.
    parse_note_body(
        "## Decided\n- dinner on 2026-10-03 Sat (Ada) [4812]\n## Open\n"
        "## Owed\n- Ben -> book -> 2026-10-01 Thu 18:30 [4815]",
        shown=SHOWN,
    )


def test_cited_ids_are_read_off_any_head_even_one_that_does_not_parse():
    assert cited_ids_in(CONFORMANT) == {4812, 4815, 4816, 4817, 4818, 4820}
    assert cited_ids_in("Ada wants dim sum. Later [12,13] and [ 14 ].") == {12, 13, 14}
    assert cited_ids_in("a legacy body with no citations [#4] [x]") == frozenset()


# --- a Rollup key is a citation ----------------------------------------------

KEY = rollup_key("stay", "p_ada", "2026-10-01T09:00:00+08:00", "2026-10-01T11:00:00+08:00")
WITH_KEYS = Shown(
    rendered=SHOWN.rendered, roster=SHOWN.roster, nora_names=SHOWN.nora_names,
    rollup_keys=frozenset({KEY}),
)


def test_a_line_may_cite_a_rollup_row_the_write_read():
    body = parse_note_body(
        f"## Decided\n## Open\n- whether Ada is home by six [rollup({KEY})]\n"
        f"- which restaurant [4812, rollup({KEY})]\n",
        shown=WITH_KEYS,
    )
    lines = body.section("Open").lines
    assert lines[0].rollup_keys == (KEY,) and lines[0].ids == ()
    assert lines[1].rollup_keys == (KEY,) and lines[1].ids == (4812,)


def test_a_rollup_row_the_write_did_not_read_is_refused():
    assert _refused(f"## Decided\n## Open\n- where [rollup({KEY})]\n") == "unknown_rollup_key"
    assert _refused(
        "## Decided\n## Open\n- where [rollup(stay|p_ada|a/b)]\n", shown=WITH_KEYS,
    ) == "unknown_rollup_key"


def test_a_group_mixing_ids_and_rollup_keys_yields_both_and_a_key_s_commas_are_not_ids():
    """The edge and the lineage of a note are read from these two: the
    messages of a mixed group must not be missed by an ids-only pattern, and a key
    carries commas (`colocation|u|p_ada,p_ben|...`) that must not be split."""
    coloc = rollup_key("colocation", "u|p_ada,p_ben", "2026-10-01T09:00:00+08:00",
                       "2026-10-01T11:00:00+08:00")
    text = f"- a [4812, rollup({KEY})]\n- b [rollup({coloc}), 7]\n- c [rollup({KEY})] [9]"
    assert cited_ids_in(text) == {4812, 7, 9}
    assert rollup_keys_in(text) == {KEY, coloc}
    assert rollup_keys_in("a legacy body [12] with [no key] rollup(stay|x|a/b)") == frozenset()
    assert cited_ids_in("[rollup(stay|p_ada|a/b), x]") == frozenset()


def test_a_note_revision_s_citations_are_what_a_memo_s_column_would_hold():
    """`note_revision.citations` is `memo_revision.citations`' form, read off
    the stored body: on a body that parses, exactly what the memo verbs store
    from the parse (a line's ids, then its keys); a bracket inside a line's
    text was never checked against what the write was shown, so it is never a
    citation; on a body that does not parse, each line's trailing brackets."""
    mixed = (
        f"## Decided\n## Open\n- which restaurant [rollup({KEY}), 4812]\n"
        f"- which [999999] room [4815]\n- and Ada again [4812]\n"
    )
    for text, shown in ((CONFORMANT, SHOWN), (mixed, WITH_KEYS)):
        assert citations_in(text) == citations_of(parse_note_body(text, shown=shown))
    assert citations_in(mixed) == ("4812", f"rollup({KEY})", "4815")
    assert citations_in("a legacy [12] body\n- with [no key]\n- and [#4] [ 007 ]") == ("7",)
    assert citations_in("- an empty key [rollup(), 5]") == ("5",)
    assert citations_in("## Decided\n## Open\n") == ()


def test_a_rollup_row_does_not_say_who_said_a_line():
    """A Rollup row is who was where, never who said what: an attributed line
    resting only on one would name an author nothing shows."""
    assert _refused(
        f"## Decided\n- dinner is on (Ada) [rollup({KEY})]\n## Open\n- y [4812]\n",
        shown=WITH_KEYS,
    ) == "misattributed"


def test_a_malformed_citation_group_is_still_uncited():
    assert _refused("## Decided\n## Open\n- where [rollup(]\n", shown=WITH_KEYS) == "uncited"
    assert _refused("## Decided\n## Open\n- where [4812, x]\n") == "uncited"


def test_every_rule_is_asserted_by_a_test_here():
    """Read off this file, not off `RULES`: a rule added to the vocabulary
    without a test that expects it fails here."""
    source = Path(__file__).read_text(encoding="utf-8")
    asserted = set(re.findall(r'== "([a-z_]+)"', source))
    assert set(RULES) <= asserted, sorted(set(RULES) - asserted)


def test_in_schema_form_reads_form_not_evidence():
    """`form`: a stored body is in the sections when it
    parses with every cited id carried and every owner a roster name. A
    legacy prose body is not; nor is one that breaks a FORM rule."""
    from nora.domain.note_body import in_schema_form

    assert in_schema_form(
        "## Decided\n## Open\n- where [1]\n## Owed\n- X -> y -> no date given [2]"
    )
    assert not in_schema_form("Ada wants dinner on Saturday.")
    assert not in_schema_form("## Decided\n## Open\n- where")  # uncited
    assert not in_schema_form("## Decided\n- on 2026-10-03 Fri (Ada) [1]\n## Open")  # bad_date


def test_dropped_citations_reads_every_head_line_against_the_new_body():
    """The carry-forward check: a line may be rewritten, moved or merged,
    as long as some new line cites ALL of its ids; a legacy head with no
    citations makes it vacuous."""
    from nora.domain.note_body import dropped_citations

    head = "## Decided\n- a (Ada) [1]\n## Open\n- b [2, 3]\n- c [4]"
    assert dropped_citations(head, "## Decided\n- a and b (Ada) [1, 2, 3]\n## Open\n- c [4]") == ()
    assert dropped_citations(head, "## Decided\n- a (Ada) [1]\n## Open\n- b [2]\n- c [4]") == (
        frozenset({2, 3}),
    )
    assert dropped_citations("Ada wants dinner.", "## Decided\n## Open\n- x [9]") == ()


def test_resolve_positions_maps_each_position_to_the_row_it_showed():
    """`[#n]` is position n of her transcript; a bare id survives only
    when the note already cites it."""
    from nora.domain.note_body import NoteBodyError, resolve_positions

    rows = [501, 502, 503]
    assert resolve_positions(
        "## Open\n- a [#3, 900]\n- b [#1]", rows=rows, carried=frozenset({900}),
    ) == "## Open\n- a [503, 900]\n- b [501]"
    for bad in ("- a [#4]", "- a [#0]", "- a [901]"):
        try:
            resolve_positions(bad, rows=rows, carried=frozenset({900}))
        except NoteBodyError as refused:
            assert refused.rule == "unknown_id"
        else:
            raise AssertionError(bad)


# ---- the memo body (DEC-306): the same line grammar --------------------------


def _memo_refused(text: str) -> str:
    from nora.domain.memo import parse_memo_body

    with pytest.raises(NoteBodyError) as err:
        parse_memo_body(text, shown=SHOWN)
    return err.value.rule


def test_a_memo_body_parses_kept_and_conditions_and_may_cite_noras_own_message():
    from nora.domain.memo import citations_of, parse_memo_body

    text = (
        "## Kept\n- the deposit is 500, split four ways [4812, 4819]\n"
        "## Conditions\n- only until 2026-12-31 [4815]"
    )
    body = parse_memo_body(text, shown=SHOWN)
    assert body.text == text
    assert citations_of(body) == ("4812", "4819", "4815")


def test_a_memo_body_breaking_the_schema_is_refused_by_rule():
    assert _memo_refused("## Kept\n- kept [4812]\n## Superseded\n- old [4812]") == "code_written"
    assert _memo_refused("## Conditions\n- weekdays [4812]") == "missing_section"
    assert _memo_refused("## Kept") == "missing_section"
    assert _memo_refused("## Conditions\n- weekdays [4812]\n## Kept\n- x [4812]") == "section_order"
    assert _memo_refused("## Decided\n- x [4812]") == "unknown_section"
    assert _memo_refused("## Kept\n- the code [9999]") == "unknown_id"
    assert _memo_refused("## Kept\n- the code") == "uncited"
    assert _memo_refused("## Kept\n- from 2026-02-30 on [4812]") == "bad_date"
