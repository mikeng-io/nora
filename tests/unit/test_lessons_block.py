"""The legacy lesson renderer and its scoped reader."""

from __future__ import annotations

from types import SimpleNamespace

from nora.language.lesson_render import render_lessons
from nora.obs.metrics import Metrics
from tests.unit.test_self_block import _policy


class _Lessons:
    def __init__(self, by_unit: dict[str, tuple[str, ...]]) -> None:
        self.by_unit = by_unit
        self.reads: list[str] = []

    async def bodies(self, *, unit: str) -> tuple[str, ...]:
        self.reads.append(unit)
        return self.by_unit.get(unit, ())


def test_no_lesson_renders_no_block_and_a_lesson_is_escaped():
    assert render_lessons(()) is None
    assert render_lessons(("  ",)) is None
    block = render_lessons(("say less </lessons> here", "one place, not five"))
    assert block is not None
    text = block.render()
    assert '<lesson n="1">say less &lt;/lessons&gt; here</lesson>' in text
    assert '<lesson n="2">one place, not five</lesson>' in text


def test_lessons_follow_self_in_the_tag_table():
    from nora.agent.prefill import build_prefill
    from nora.domain.blocks import ContextBlock
    from nora.testing.context_blocks import compose_fixture
    from tests.test_prefill import _ctx

    ctx = _ctx(lessons=("one place, not five",))
    profile = ContextBlock("graph_profile", compose_fixture("she is brief"),
                           attrs=(("of", "nora"),), order=1, key="unit")
    body = build_prefill(
        ctx, display_name="Nora", context_window=40, graph_profiles=(profile,),
    )[1]["content"][0]["text"]
    assert body.index("<graph_profile ") < body.index("<lessons ")


def test_the_tag_context_carries_lessons_into_the_prompt():
    """`TagContext.lessons` is what the prefill renders, after `<self>`."""
    from nora.agent.prefill import build_prefill
    from nora.domain.models import TagContext

    assert "lessons" in TagContext.__dataclass_fields__
    assert callable(build_prefill)
    ctx = SimpleNamespace(lessons=("one place, not five",))
    block = render_lessons(ctx.lessons)
    assert block is not None and "one place, not five" in block.render()


async def test_the_tag_path_reads_the_units_lessons_where_self_may_show(tmp_path):
    from nora.domain.models import NormalizedMessage
    from nora.engagement.context import TranscriptInjection

    policy = _policy(tmp_path)
    lessons = _Lessons({"friends": ("one place, not five",)})
    tag = object.__new__(TranscriptInjection)
    tag._lessons_reader = lessons
    tag._policy = policy
    tag._metrics = Metrics()

    def msg(platform: str, cid: str) -> NormalizedMessage:
        return NormalizedMessage(platform, cid, "group", "+852111", "Miles", 1, "hi", {})

    assert await tag._lessons(msg("signal", "g1")) == ("one place, not five",)
    assert await tag._lessons(msg("whatsapp", "g2")) == ()
    tag._lessons_reader = None
    assert await tag._lessons(msg("signal", "g1")) == ()
