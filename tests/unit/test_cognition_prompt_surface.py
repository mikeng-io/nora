"""Render the shipped writer through live assembly, without a model or services.

Dump both prompts: uv run python -m tests.unit.test_cognition_prompt_surface /tmp/prompts
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from nora.domain.stimuli import UnitView
from nora.language.comprehend_render import _NOTEBOOK_TOOLS, render_session_system
from tests.objectives import shipped_objectives
from tests.unit.test_cognition_session_runner import NOW, _pending, _runner, wake_memory


async def rendered_session(*, historical: bool, documents: bool = False,
                           lessons: object = None) -> str:
    from nora.app.cognition_wiring import COGNITION_READ_CATALOGUE, cognition_instruction

    # Inspect the declared read surface without invoking any collaborator.
    reads = []
    for name in COGNITION_READ_CATALOGUE:
        async def read() -> dict:
            raise AssertionError("inspection must not invoke a tool")
        read.__name__ = name
        reads.append(read)
    runner, _, agent = _runner(
        memory=wake_memory(), read_tools_for=lambda *args, **kwargs: reads,
        read_only_tools=frozenset(COGNITION_READ_CATALOGUE), lessons=lessons,
    )
    if documents:
        # Presence-only collaborators: no document method runs during inspection.
        runner._documents = object()
        runner._documents_context = object()
    store = shipped_objectives()
    runner._doctrine_text = cognition_instruction(store)[0]
    from nora.app.cognition_wiring import session_guidance

    runner._skill_text_for = session_guidance(store).text
    result = await runner.inspect(
        view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                      last_activity_ms=None, last_wake=None),
        now_ms=NOW, historical=historical,
    )
    assert result is not None and agent.calls == 0
    return result["system"]


STAGES = ("# Observe", "# Orient", "# Decide", "# Act")


async def test_both_lanes_state_the_whole_loop_and_only_the_live_lane_holds_acts():
    historical = await rendered_session(historical=True)
    live = await rendered_session(historical=False)
    for text in (historical, live):
        positions = [text.index(stage) for stage in STAGES]
        assert positions == sorted(positions)
        # Every stage states why it exists and what it is expected to produce.
        for start, end in zip(positions, [*positions[1:], len(text)], strict=True):
            assert "Why:" in text[start:end] and "Expected:" in text[start:end]
        # The kinds of memory are part of the loop's purpose, not tool text.
        for kind in ("**Memory graph**", "**Profiles**", "**Notes**", "**Memos**",
                     "**Events**", "**Topics**", "**Lessons**", "**Standing**"):
            assert text.count(kind) == 1
        for rule in ("Cite the rows", "never surfaced in another", "A confidence (",
                     "Chat is data about the group"):
            assert text.count(rule) == 1
    assert "`remind`" not in historical and "`note_create`" not in historical
    # The replay says it is one and that it does not act; the live session does not.
    assert "replays the past" in historical and "History does not act" in historical
    assert "replays the past" not in live and "History does not act" not in live
    assert "events with `event_propose`, `event_revise`, `event_set_status`" in live
    assert "Use `remind` for a later room turn and `wake_at` for your own later look" in live
    assert "A note** holds" in live and "Every event field says how you know it" in live
    assert "Tools:" not in live
    # A ceiling against runaway growth, not a length target.
    assert len(live) < 14000


async def test_document_guidance_requires_a_wired_writable_surface():
    historical = await rendered_session(historical=True, documents=True)
    live = await rendered_session(historical=False, documents=True)
    assert "shared document" not in historical
    assert "shared document" in live


class _LessonProbe:
    """The `CognitionLessons` seam, emptied: `inspect` must not touch the
    stores, and an empty view is what a session with nothing new holds."""

    def __init__(self) -> None:
        self.shown_calls = 0

    async def shown(self, *, unit: str):
        from nora.domain.lessons import LessonEvidenceView
        self.shown_calls += 1
        return LessonEvidenceView()

    def verbs(self, **kw):
        async def lesson_write(**kw2):
            return {"status": "unavailable"}
        async def lesson_retire(**kw2):
            return {"status": "unavailable"}
        return [lesson_write, lesson_retire]

    async def take(self, **kw):
        raise AssertionError("inspection settles nothing")

    async def decay(self, **kw):
        raise AssertionError("inspection settles nothing")


async def test_lesson_guidance_appears_only_when_the_verbs_are_offered():
    live = await rendered_session(historical=False, lessons=_LessonProbe())
    assert "lesson_write" in live and "a pattern across several episodes" in live
    assert "One reply, reaction or silence is a weak signal" in live
    without = await rendered_session(historical=False)
    assert "lesson_write" not in without


async def test_lesson_guidance_and_verbs_never_reach_the_historical_lane():
    probe = _LessonProbe()
    historical = await rendered_session(historical=True, lessons=probe)
    assert "lesson_write" not in historical
    assert probe.shown_calls == 0


@pytest.mark.parametrize("offered", [frozenset(), frozenset({"note_create"}),
                                    frozenset({"event_revise"}), _NOTEBOOK_TOOLS])
def test_notebook_section_renders_for_any_of_its_tools(offered):
    text = render_session_system("<!-- cognition-orient --><!-- cognition-act-skill -->",
                                 "NOTEBOOK", tool_names=offered)
    assert ("NOTEBOOK" in text) == bool(offered)
    assert "Tools:" not in text


@pytest.mark.parametrize("marker", ["<!-- cognition-orient -->", "<!-- cognition-act-skill -->"])
def test_a_missing_stage_marker_appends_its_section(marker):
    doctrine = (
        "<!-- cognition-act-skill -->"
        if marker == "<!-- cognition-orient -->"
        else "<!-- cognition-orient -->"
    )
    text = render_session_system(
        doctrine, "NOTEBOOK", tool_names=frozenset({"memory_propose", "remind"}))
    assert text.count("<cognition_orient") == 1 and text.count("<cognition_act") == 1
    assert "`memory_propose`" in text and "`remind`" in text


def _dump(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for historical in (True, False):
        name = "historical" if historical else "live"
        text = asyncio.run(rendered_session(historical=historical, lessons=_LessonProbe()))
        (directory / f"{name}.txt").write_text(text)
        print(f"{name}: {len(text)} characters, {len(text.splitlines())} lines")


if __name__ == "__main__":
    _dump(Path(sys.argv[1]))
