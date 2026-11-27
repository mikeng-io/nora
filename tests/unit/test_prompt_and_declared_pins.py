"""Small renderer guarantees every prompt leans on."""

import pytest

from nora.domain.blocks import ContextBlock
from nora.domain.blocks_declared import BlockDeclaration, assemble_declared
from nora.domain.prompt import clip, contains_source_span
from nora.testing.context_blocks import compose_fixture


def test_a_clipped_message_is_one_line_even_when_it_fits():
    assert clip("first\n  second\nthird", 80) == "first second third"
    assert "\n" not in clip("first\nsecond\nthird", 8)


def test_a_copied_quote_may_use_the_comma_spelling_of_an_em_dash():
    assert contains_source_span("link, nobody answered", "link — nobody answered")
    assert not contains_source_span("link; nobody answered", "link — nobody answered")


def test_a_block_that_emits_a_field_its_type_does_not_explain_is_refused():
    declaration = [BlockDeclaration("clock", 0, "Interpret evidence at the pass instant.")]
    block = ContextBlock(
        name="clock", body=compose_fixture("now"), attrs=(("invented_field", "x"),),
    )
    with pytest.raises(ValueError, match="undescribed fields"):
        assemble_declared(declaration, [block])
