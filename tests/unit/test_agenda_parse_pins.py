"""Reading a rendered agenda back: reaction rows are context, never new source evidence."""

from nora.language.cognition_parse import rendered_new_sources, shown_source_rows

AGENDA = (
    '<item kind="message" room="«Dinner»">'
    '<m n="1">we meet at 7</m>'
    '<m n="2" reaction="yes">👍</m>'
    "</item>"
)


def test_a_reaction_row_is_not_new_source_evidence():
    assert set(rendered_new_sources(AGENDA)) == {"1"}


def test_a_reaction_row_is_not_a_source_row_the_judge_may_cite():
    assert set(shown_source_rows(AGENDA)) == {"1"}
