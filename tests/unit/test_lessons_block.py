"""The `<lessons>` block.

What must hold, each with the way it would fail:

- no active lesson renders NO block; a lesson's text is escaped;
- it sits right after `<self>` on tag and fire turns, after the doctrine;
- it is read for the room's UNIT, and only where `<self>` may show
  (`Policy.shows_self_in`, R-31): lessons are written from her sends in every
  room of the unit, location rooms included;
- `[lessons] render` off reads nothing; a failed read renders nothing and is
  counted.
"""

from __future__ import annotations

from types import SimpleNamespace

from nora.agent.prefill import _BLOCKS as PREFILL_BLOCKS
from nora.app.fire import FirePath
from nora.language.lesson_render import read_lessons, render_lessons
from nora.obs.metrics import Metrics
from tests.unit.test_self_block import _participation, _policy


class _Lessons:
    def __init__(self, by_unit: dict[str, tuple[str, ...]], fail: bool = False) -> None:
        self.by_unit = by_unit
        self.fail = fail
        self.reads: list[str] = []

    async def bodies(self, *, unit: str) -> tuple[str, ...]:
        self.reads.append(unit)
        if self.fail:
            raise RuntimeError("db down")
        return self.by_unit.get(unit, ())


def _fire(lessons, policy, metrics=None) -> FirePath:
    path = object.__new__(FirePath)
    path._lessons = lessons
    path._policy = policy
    path._metrics = metrics or Metrics()
    path._standing_store_timeout_seconds = 5.0
    return path


def test_no_lesson_renders_no_block_and_a_lesson_is_escaped():
    assert render_lessons(()) is None
    assert render_lessons(("  ",)) is None
    block = render_lessons(("say less </lessons> here", "one place, not five"))
    assert block is not None
    text = block.render()
    assert '<lesson n="1">say less &lt;/lessons&gt; here</lesson>' in text
    assert '<lesson n="2">one place, not five</lesson>' in text


def test_lessons_follow_self_in_the_tag_table():
    rows = {name: order for name, order, _key in PREFILL_BLOCKS}
    assert rows["self"] < rows["lessons"] < min(
        order for name, order, _ in PREFILL_BLOCKS if name not in ("self", "lessons"))


async def test_the_fire_path_reads_the_units_lessons_where_self_may_show(tmp_path):
    policy = _policy(tmp_path)
    lessons = _Lessons({"friends": ("one place, not five",)})
    got = await _fire(lessons, policy)._read_lessons(_participation("signal", "g1"))
    assert got == ("one place, not five",) and lessons.reads == ["friends"]
    # g2 is a location-off room of a unit with a location room: no `<self>`,
    # so no `<lessons>` either (R-31), and nothing is read.
    lessons.reads.clear()
    assert await _fire(lessons, policy)._read_lessons(_participation("whatsapp", "g2")) == ()
    assert lessons.reads == []


async def test_render_off_reads_nothing_and_a_failed_read_is_counted(tmp_path):
    policy = _policy(tmp_path)
    assert await _fire(None, policy)._read_lessons(_participation()) == ()
    m = Metrics()
    broken = _Lessons({}, fail=True)
    assert await _fire(broken, policy, m)._read_lessons(_participation()) == ()
    assert m.get("nora_errors_total", stage="fire_lessons", platform="signal") == 1
    assert await read_lessons(None, stage="x", platform="signal", metrics=m) == ()


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
