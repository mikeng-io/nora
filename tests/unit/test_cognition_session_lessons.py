"""Lesson verbs through the session runner — a live session is offered
`lesson_write`/`lesson_retire` citing shown episodes only, a historical one
is offered neither, and the batch is consumed only when the session ends.
Split from `test_cognition_session_runner.py` beside
`test_cognition_session_admission.py`.
"""

from __future__ import annotations

import pytest

from nora.domain.stimuli import UnitView
from tests.unit.test_cognition_session_runner import (
    NOW,
    FakeCognitionAgentRunner,
    _pending,
    _runner,
)


def _lesson_seam(quiet_s: int | None = None):
    """The real `CognitionLessons` over the in-memory fakes — checked writes only."""
    from nora.agent.dream_screen import offence
    from nora.domain.lessons import LESSON_MIN_EPISODES  # noqa: F401 - shape note
    from nora.language.character import CharacterRoom
    from nora.language.lesson_book import LessonBook
    from nora.language.lessons import (
        CognitionLessons,
        LessonEvidence,
        LessonsKey,
    )
    from nora.obs.metrics import Metrics
    from nora.testing import InMemoryCharacterEvidence
    from tests.unit.test_cognition_lessons import _Clock, _Cursor, _Store

    clock = _Clock(NOW)
    store = _Store()
    book = LessonBook(store=store, law=lambda: ["Never share a location."],
                      screen=offence, clock_ms=clock)
    evidence_store = InMemoryCharacterEvidence()
    keys = {"unit-1": LessonsKey(unit="unit-1", rooms=(
        CharacterRoom(key="home-room", platform="signal", label="Home"),))}
    evidence = LessonEvidence(
        store=evidence_store, book=book, keys=keys, clock_ms=clock,
        correction_quiet_seconds=quiet_s)
    cursor = _Cursor(evidence_store, clock)
    lessons = CognitionLessons(
        evidence=evidence, book=book, keys=keys, cursor=cursor,
        batch_limit=100, horizon_seconds=5_184_000,
        name_of=lambda pid: "someone", clock_ms=clock, metrics=Metrics())
    return lessons, store, evidence_store, cursor


def _seed_episodes(evidence_store, *send_ids: int) -> None:
    from nora.domain.culture import Episode

    for sid in send_ids:
        evidence_store.add_episode(unit="unit-1", episode=Episode(
            send_id=sid, room="home-room", text="dinner at 8?",
            outcome="responded", reactions=1, edits=0))


async def test_a_live_session_is_offered_lesson_verbs_and_writes_citing_a_shown_episode():
    lessons, _store, evidence_store, cursor = _lesson_seam()
    _seed_episodes(evidence_store, 100, 110, 120)
    fake = FakeCognitionAgentRunner(script=[(
        "lesson_write",
        {"body": "when someone asks where to eat, name one place and why",
         "citations": ["response:100", "response:110", "response:120"]},
    )])
    runner, _, fake = _runner(agent_runner=fake, lessons=lessons)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert {"lesson_write", "lesson_retire"} <= set(fake.last_tool_names)
    assert fake.results[0]["status"] == "ok"
    [lesson] = await lessons._book.active(unit="unit-1")
    assert lesson.head.established_by == "cognition:unit-1"
    assert lesson.head.instruction_sha256 == "sha"
    assert cursor.at == 120


async def test_a_lesson_citing_an_episode_the_session_was_not_shown_is_refused():
    lessons, _store, evidence_store, _cursor = _lesson_seam()
    _seed_episodes(evidence_store, 100, 110, 120)
    fake = FakeCognitionAgentRunner(script=[(
        "lesson_write",
        {"body": "when someone asks where to eat, name one place and why",
         "citations": ["response:999", "response:110", "response:120"]},
    )])
    runner, _, fake = _runner(agent_runner=fake, lessons=lessons)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    out = fake.results[0]
    assert (out["status"], out["reason"]) == ("refused", "unresolved_citation")
    assert await lessons._book.active(unit="unit-1") == ()


async def test_closed_episodes_reach_the_session_context_with_citable_ids():
    lessons, _store, evidence_store, _cursor = _lesson_seam()
    _seed_episodes(evidence_store, 100, 110, 120)
    fake = FakeCognitionAgentRunner(script=[])
    runner, _, fake = _runner(agent_runner=fake, lessons=lessons)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    for n in (100, 110, 120):
        assert f'n="response:{n}"' in fake.last_prompt
    assert "<lesson_episodes" in fake.last_prompt


async def test_a_historical_session_neither_reads_nor_holds_lesson_verbs():
    class _Spy:
        def __init__(self, inner):
            self.inner = inner
            self.shown_calls = 0
            self.take_calls = 0
            self.decay_calls = 0

        async def shown(self, *, unit):
            self.shown_calls += 1
            return await self.inner.shown(unit=unit)

        def verbs(self, **kw):
            return self.inner.verbs(**kw)

        async def take(self, **kw):
            self.take_calls += 1
            return await self.inner.take(**kw)

        async def decay(self, **kw):
            self.decay_calls += 1
            return await self.inner.decay(**kw)

    lessons, _store, evidence_store, _cursor = _lesson_seam()
    _seed_episodes(evidence_store, 100, 110, 120)
    spy = _Spy(lessons)
    fake = FakeCognitionAgentRunner(script=[])
    runner, _, fake = _runner(agent_runner=fake, lessons=spy)
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW, historical=True)
    assert not {"lesson_write", "lesson_retire"} & set(fake.last_tool_names)
    assert (spy.shown_calls, spy.take_calls, spy.decay_calls) == (0, 0, 0)
    assert "response:100" not in fake.last_prompt


async def test_a_failed_session_does_not_consume_the_lesson_batch():
    lessons, _store, evidence_store, cursor = _lesson_seam()
    _seed_episodes(evidence_store, 100, 110, 120)

    class _Doomed(FakeCognitionAgentRunner):
        async def run(self, **kw):
            raise RuntimeError("model unreachable")

    fake = _Doomed()
    runner, _store, fake = _runner(agent_runner=fake, lessons=lessons)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    with pytest.raises(RuntimeError):
        await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert cursor.at == 0


async def test_the_recorded_session_row_carries_the_lesson_evidence_it_was_shown():
    """A live session's grounding — the provenance the row persists — carries
    the evidence the session was shown: the episode send ids, the correction
    row ids and the cursor `take` would write."""
    lessons, _store, evidence_store, _cursor = _lesson_seam(quiet_s=60)
    _seed_episodes(evidence_store, 100, 110, 120)
    sent = NOW - 300_000
    evidence_store.add_message(id=500, platform="signal", room="home-room",
                               person_id=None, body="five options", ts=sent,
                               is_nora=True)
    evidence_store.add_message(id=501, platform="signal", room="home-room",
                               person_id="ada", body="stop giving five options",
                               ts=NOW - 200_000, quoted_ts=sent)
    expected = await lessons.shown(unit="unit-1")
    runner, store, _ = _runner(
        agent_runner=FakeCognitionAgentRunner(script=[]), lessons=lessons)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    _, session, _ = store.sessions[-1]
    grounding = session.situation["grounding"]
    assert grounding["lesson_episodes"] == [
        e.send_id for e in expected.episodes] == [100, 110, 120]
    assert grounding["lesson_corrections"] == [
        m.row.id for m in expected.corrections] == [501]
    assert grounding["lessons_cursor"] == expected.cursor
