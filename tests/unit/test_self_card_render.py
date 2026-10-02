from __future__ import annotations

from nora.language.self_card_render import render_self_card


def test_no_conclusions_renders_no_block():
    """An unreachable peer or one Honcho holds nothing about is not a peer
    she has an EMPTY card about — the block does not render at all."""
    assert render_self_card(()) is None


def test_conclusions_render_as_bullets():
    block = render_self_card(("nora agreed to bring dessert",))
    assert block is not None
    assert block.name == "self_card"
    text = block.render()
    assert text.startswith("<self_card ")
    assert "- nora agreed to bring dessert" in text


def test_every_conclusion_gets_its_own_line():
    block = render_self_card(("first thing", "second thing"))
    assert block is not None
    assert "- first thing" in block.render()
    assert "- second thing" in block.render()


def test_this_is_the_one_builder_the_tag_turn_calls():
    """`design/context-assembly/01-the-registry.md` S3: `nora.agent.prefill`
    and the proactive pass used to build `ContextBlock(name="self_card",
    body=render_bullets(conclusions))` by hand, the same computation on the
    same input type written twice with nothing comparing them. The proactive
    pass is gone (DEC-277); the tag turn is the one caller left, and it must
    keep calling this module's builder rather than a copy. Falsification: the
    import failing means it stopped sharing this module.
    """
    from nora.agent.prefill import render_self_card as prefill_render_self_card

    assert prefill_render_self_card is render_self_card
